"""
Phase 8: Qdrant 1.15.x Benchmark

Infrastructure: Qdrant v1.15.0
  - HNSW cosine (BGE-M3 1024-dim dense vectors)
  - Sparse vector BM25 (MeCab tokenization + IDF modifier)
  - Text payload index (multilingual tokenizer) — filtering, NOT ranked BM25

Note on Qdrant BM25 modes:
  - BM25-MeCab: Sparse vector with python-mecab-ko tokenization + Modifier.IDF
                True BM25-like scoring (TF * IDF weighted dot product)
  - Text-builtin: Payload text index with TokenizerType.MULTILINGUAL
                  Unicode word boundary tokenization (charabia), NOT morphological.
                  Used for full-text filter-based retrieval. Not ranked BM25.

Methods:
  1. BM25-MeCab    : Sparse vector, MeCab tokenization (형태소 분석)
  2. BM25-builtin  : Text payload filter, multilingual tokenizer (Unicode)
  3. Dense         : HNSW cosine (BGE-M3, retrieval-only*)
  4. Hybrid-MeCab  : Sparse (MeCab) + Dense prefetch, Qdrant RRF
  5. Hybrid-builtin: Text filter + Dense (manual RRF, best-effort)

*retrieval-only: BGE-M3 inference excluded, query embeddings pre-computed

Datasets: MIRACL-ko 10K (213 queries), EZIS 97 (131 queries)
Metrics:  NDCG@10, Recall@10, MRR, latency p50/p95/p99

Usage:
  # Start Qdrant first:
  #   docker compose --profile phase8-qdrant up -d
  uv run python3 experiments/phase8_system_comparison/phase8_qdrant.py \\
    --qdrant-url http://localhost:6333 \\
    --output-dir results/phase8
"""

import argparse
import json
import math
import os
import time
from collections import Counter, defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    HnswConfigDiff,
    MatchText,
    Modifier,
    OptimizersConfigDiff,
    PointStruct,
    Prefetch,
    SparseIndexParams,
    SparseVector,
    SparseVectorParams,
    TextIndexParams,
    TokenizerType,
    VectorParams,
)

try:
    from mecab import MeCab
    _mecab = MeCab()
    MECAB_AVAILABLE = True
except ImportError:
    MECAB_AVAILABLE = False
    print("[WARN] python-mecab-ko not available. BM25-MeCab mode disabled.")

MIRACL_QUERIES_PATH = "data/miracl/queries_dev.json"
EZIS_QUERIES_PATH   = "data/ezis/queries.json"
EMB_MIRACL_PATH     = "data/phase8/query_embs_miracl.json"
EMB_EZIS_PATH       = "data/phase8/query_embs_ezis.json"
DOC_EMB_MIRACL_PATH = "data/phase8/doc_embs_miracl.json"
DOC_EMB_EZIS_PATH   = "data/phase8/doc_embs_ezis.json"
N_MIRACL = 213
TOPK     = 60
WARMUP   = 5
BATCH    = 256


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def ndcg_at_k(relevant_ids: List[str], retrieved_ids: List[str], k: int = 10) -> float:
    relevant_set = set(relevant_ids)
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, doc_id in enumerate(retrieved_ids[:k], start=1)
        if doc_id in relevant_set
    )
    ideal_hits = min(len(relevant_set), k)
    idcg = sum(1.0 / math.log2(r + 1) for r in range(1, ideal_hits + 1))
    return dcg / idcg if idcg > 0 else 0.0


def recall_at_k(relevant_ids: List[str], retrieved_ids: List[str], k: int = 10) -> float:
    relevant_set = set(relevant_ids)
    hits = sum(1 for doc_id in retrieved_ids[:k] if doc_id in relevant_set)
    return hits / len(relevant_set) if relevant_set else 0.0


def mrr_at_k(relevant_ids: List[str], retrieved_ids: List[str], k: int = 10) -> float:
    relevant_set = set(relevant_ids)
    for rank, doc_id in enumerate(retrieved_ids[:k], start=1):
        if doc_id in relevant_set:
            return 1.0 / rank
    return 0.0


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_queries(path: str, limit: Optional[int] = None) -> List[dict]:
    with open(path, encoding="utf-8") as f:
        qs = json.load(f)
    return qs[:limit] if limit else qs


def load_embeddings(path: str) -> Dict[str, List[float]]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# MeCab tokenization
# ---------------------------------------------------------------------------

def tokenize_mecab(text: str) -> List[str]:
    """Tokenize Korean text with MeCab, extract nouns and content words."""
    if not MECAB_AVAILABLE:
        return text.split()
    nodes = _mecab.parse(text)
    tokens = []
    for node in nodes:
        surface = node.surface.strip()
        if not surface:
            continue
        feat = str(node.feature) if node.feature else ""
        pos = feat.split(",")[0]
        # Keep nouns (NN*), verbs (VV, VA stems), foreign words (SL)
        if pos.startswith("NN") or pos in ("VV", "VA", "SL", "XR"):
            tokens.append(surface)
    return tokens if tokens else text.split()


def build_vocab(docs: List[dict]) -> Dict[str, int]:
    vocab: Dict[str, int] = {}
    for doc in docs:
        for token in tokenize_mecab(doc["text"]):
            if token not in vocab:
                vocab[token] = len(vocab)
    return vocab


def doc_to_sparse(text: str, vocab: Dict[str, int]) -> Tuple[List[int], List[float]]:
    """Convert text to sparse TF vector using MeCab tokenization."""
    tokens = tokenize_mecab(text)
    tf = Counter(tokens)
    indices, values = [], []
    for token, count in tf.items():
        if token in vocab:
            indices.append(vocab[token])
            values.append(float(count))
    return indices, values


# ---------------------------------------------------------------------------
# Collection setup
# ---------------------------------------------------------------------------

def setup_collection(client: QdrantClient, name: str, docs: List[dict],
                     doc_embeddings: Dict[str, List[float]],
                     vocab: Optional[Dict[str, int]]) -> float:
    if client.collection_exists(name):
        client.delete_collection(name)

    sparse_config = {}
    if vocab:
        sparse_config["bm25_mecab"] = SparseVectorParams(
            index=SparseIndexParams(on_disk=False),
            modifier=Modifier.IDF,
        )

    client.create_collection(
        collection_name=name,
        vectors_config={
            "dense": VectorParams(
                size=1024,
                distance=Distance.COSINE,
                hnsw_config=HnswConfigDiff(m=16, ef_construct=200),
            )
        },
        sparse_vectors_config=sparse_config if sparse_config else None,
        optimizers_config=OptimizersConfigDiff(indexing_threshold=0),
    )

    # Text payload index for builtin multilingual tokenizer
    client.create_payload_index(
        collection_name=name,
        field_name="text",
        field_schema=TextIndexParams(
            type="text",
            tokenizer=TokenizerType.MULTILINGUAL,
        ),
    )

    t0 = time.perf_counter()
    points = []
    for i, doc in enumerate(docs):
        dense_emb = doc_embeddings.get(doc["id"], [0.0] * 1024)
        vectors: dict = {"dense": dense_emb}
        if vocab:
            idx, vals = doc_to_sparse(doc["text"], vocab)
            if idx:
                vectors["bm25_mecab"] = {"indices": idx, "values": vals}

        points.append(PointStruct(
            id=i,
            vector=vectors,
            payload={"doc_id": doc["id"], "text": doc["text"]},
        ))

        if len(points) >= BATCH:
            client.upsert(collection_name=name, points=points)
            points = []

    if points:
        client.upsert(collection_name=name, points=points)

    # Wait for indexing
    client.update_collection(
        collection_name=name,
        optimizers_config=OptimizersConfigDiff(indexing_threshold=20000),
    )
    return round(time.perf_counter() - t0, 2)


# ---------------------------------------------------------------------------
# Search functions
# ---------------------------------------------------------------------------

def search_bm25_mecab(client: QdrantClient, name: str,
                      query_text: str, vocab: Dict[str, int], k: int = 10) -> List[str]:
    indices, values = doc_to_sparse(query_text, vocab)
    if not indices:
        return []
    results = client.query_points(
        collection_name=name,
        query=SparseVector(indices=indices, values=values),
        using="bm25_mecab",
        limit=k,
        with_payload=["doc_id"],
    )
    return [r.payload["doc_id"] for r in results.points]


def search_text_builtin(client: QdrantClient, name: str,
                        query_text: str, k: int = 10) -> List[str]:
    results, _ = client.scroll(
        collection_name=name,
        scroll_filter=Filter(
            must=[FieldCondition(key="text", match=MatchText(text=query_text))]
        ),
        limit=k,
        with_payload=["doc_id"],
    )
    return [r.payload["doc_id"] for r in results]


def search_dense(client: QdrantClient, name: str,
                 query_emb: List[float], k: int = 10) -> List[str]:
    results = client.query_points(
        collection_name=name,
        query=query_emb,
        using="dense",
        limit=k,
        with_payload=["doc_id"],
    )
    return [r.payload["doc_id"] for r in results.points]


def search_hybrid_mecab(client: QdrantClient, name: str,
                        query_text: str, query_emb: List[float],
                        vocab: Dict[str, int], k: int = 10) -> List[str]:
    indices, values = doc_to_sparse(query_text, vocab)
    if not indices:
        return search_dense(client, name, query_emb, k)
    results = client.query_points(
        collection_name=name,
        prefetch=[
            Prefetch(
                query=SparseVector(indices=indices, values=values),
                using="bm25_mecab",
                limit=TOPK,
            ),
            Prefetch(
                query=query_emb,
                using="dense",
                limit=TOPK,
            ),
        ],
        query=FusionQuery(fusion=Fusion.RRF),
        limit=k,
        with_payload=["doc_id"],
    )
    return [r.payload["doc_id"] for r in results.points]


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

def bench_dataset(client: QdrantClient, dataset_name: str,
                  queries: List[dict], query_embs: Dict[str, List[float]],
                  collection_name: str,
                  vocab: Optional[Dict[str, int]]) -> List[dict]:
    print(f"\n  === {dataset_name} | collection={collection_name} | {len(queries)} queries ===")

    methods = []
    if vocab and MECAB_AVAILABLE:
        methods.append(("BM25-MeCab",     lambda t, e: search_bm25_mecab(client, collection_name, t, vocab)))
    methods.append(("Text-builtin",   lambda t, e: search_text_builtin(client, collection_name, t)))
    methods.append(("Dense",          lambda t, e: search_dense(client, collection_name, e) if e else []))
    if vocab and MECAB_AVAILABLE:
        methods.append(("Hybrid-MeCab",   lambda t, e: search_hybrid_mecab(client, collection_name, t, e, vocab) if e else []))

    results = []
    for name, fn in methods:
        ndcg_s, rec_s, mrr_s, lats = [], [], [], []

        for q in queries[:WARMUP]:
            fn(q["text"], query_embs.get(q["query_id"]))

        for q in queries:
            emb = query_embs.get(q["query_id"])
            t0 = time.perf_counter()
            retrieved = fn(q["text"], emb)
            lats.append((time.perf_counter() - t0) * 1000)

            rel = q.get("relevant_ids", [])
            if rel:
                ndcg_s.append(ndcg_at_k(rel, retrieved))
                rec_s.append(recall_at_k(rel, retrieved))
                mrr_s.append(mrr_at_k(rel, retrieved))

        lats.sort()
        n = len(lats)
        nq = len(ndcg_s)

        r = {
            "system": "qdrant",
            "dataset": dataset_name,
            "method": name,
            "n_queries": len(queries),
            "ndcg_at_10":   round(sum(ndcg_s) / nq, 4) if nq else None,
            "recall_at_10": round(sum(rec_s) / nq, 4)  if nq else None,
            "mrr":          round(sum(mrr_s) / nq, 4)  if nq else None,
            "latency_p50":  round(lats[n // 2], 2),
            "latency_p95":  round(lats[int(n * 0.95)], 2),
            "latency_p99":  round(lats[int(n * 0.99)], 2),
        }
        print(f"  [{name:14s}] NDCG@10={r['ndcg_at_10']}  "
              f"Recall={r['recall_at_10']}  MRR={r['mrr']}  "
              f"p50={r['latency_p50']}ms")
        results.append(r)

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--output-dir", default="results/phase8")
    args = parser.parse_args()

    print("=" * 60)
    print("Phase 8: Qdrant 1.15.x Benchmark")
    print("=" * 60)
    print(f"  MeCab available: {MECAB_AVAILABLE}")

    client = QdrantClient(url=args.qdrant_url, timeout=60)

    miracl_queries = load_queries(MIRACL_QUERIES_PATH, limit=N_MIRACL)
    ezis_queries   = load_queries(EZIS_QUERIES_PATH)
    miracl_q_embs  = load_embeddings(EMB_MIRACL_PATH)
    ezis_q_embs    = load_embeddings(EMB_EZIS_PATH)

    # Load document embeddings
    miracl_d_embs  = load_embeddings(DOC_EMB_MIRACL_PATH)
    ezis_d_embs    = load_embeddings(DOC_EMB_EZIS_PATH)

    miracl_corpus_path = "data/miracl/corpus_10k.json"
    miracl_docs = []
    if os.path.exists(miracl_corpus_path):
        with open(miracl_corpus_path, encoding="utf-8") as f:
            miracl_docs = json.load(f)
    print(f"  MIRACL corpus: {len(miracl_docs)} docs, doc_embs: {len(miracl_d_embs)}")

    ezis_corpus_path = "data/ezis/corpus.json"
    ezis_docs = []
    if os.path.exists(ezis_corpus_path):
        with open(ezis_corpus_path, encoding="utf-8") as f:
            ezis_docs = json.load(f)
    print(f"  EZIS corpus: {len(ezis_docs)} docs, doc_embs: {len(ezis_d_embs)}")

    all_results = []

    # MIRACL
    if miracl_docs:
        print("\n--- Building MIRACL vocabulary (MeCab) ---")
        miracl_vocab = build_vocab(miracl_docs) if MECAB_AVAILABLE else None
        if miracl_vocab:
            print(f"  Vocab size: {len(miracl_vocab)}")

        print("--- Setting up MIRACL collection ---")
        t_build = setup_collection(client, "p8_qdrant_miracl",
                                   miracl_docs, miracl_d_embs, miracl_vocab)
        print(f"  Collection built in {t_build}s")
        all_results += bench_dataset(
            client, "MIRACL", miracl_queries, miracl_q_embs,
            "p8_qdrant_miracl", miracl_vocab
        )

    # EZIS
    if ezis_docs:
        print("\n--- Building EZIS vocabulary (MeCab) ---")
        ezis_vocab = build_vocab(ezis_docs) if MECAB_AVAILABLE else None
        if ezis_vocab:
            print(f"  Vocab size: {len(ezis_vocab)}")

        print("--- Setting up EZIS collection ---")
        t_build = setup_collection(client, "p8_qdrant_ezis",
                                   ezis_docs, ezis_d_embs, ezis_vocab)
        print(f"  Collection built in {t_build}s")
        all_results += bench_dataset(
            client, "EZIS", ezis_queries, ezis_q_embs,
            "p8_qdrant_ezis", ezis_vocab
        )

    os.makedirs(args.output_dir, exist_ok=True)
    json_path = os.path.join(args.output_dir, "phase8_qdrant.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "generated": datetime.now().isoformat(),
            "system": "qdrant",
            "mecab_available": MECAB_AVAILABLE,
            "results": all_results,
        }, f, indent=2, ensure_ascii=False)
    print(f"\n  JSON: {json_path}")
    print("Done.")


if __name__ == "__main__":
    main()
