"""Phase 4: BM25 vs neural sparse (BGE-M3) — full combination matrix.

Standalone:
  *-bm25-kiwi          BM25 kiwi-cong (pgvector-sparse / in-memory)
  *-bgem3-sparse       BGE-M3 sparse (neural)
  *-bgem3-dense        BGE-M3 dense  (cosine)

BM25 × BGE-M3 sparse:
  *-hybrid-rrf         RRF (k=60)
  *-bayes-sparse       Bayesian log-odds fusion

BM25 × BGE-M3 dense:
  *-hybrid-rrf-dense   RRF (k=60)
  *-bayes-dense        Bayesian log-odds fusion (cognica-io/bayesian-bm25)

Both datasets: MIRACL-ko (10k corpus, 213 queries) and EZIS (97 docs, 131 queries).

Usage:
    uv pip install bayesian-bm25
    uv run python3 experiments/phase4_bm25_vs_neural/phase4_comparison.py \\
        --db-url postgresql://postgres:postgres@localhost:5432/dev \\
        --output-dir results/phase4
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

import psycopg2
from pgvector.psycopg2 import register_vector


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def ndcg_at_k(ranked: List[str], rel: set, k: int = 10) -> float:
    dcg = sum(1.0 / math.log2(r + 2) for r, d in enumerate(ranked[:k]) if d in rel)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(min(len(rel), k)))
    return dcg / idcg if idcg else 0.0

def recall_at_k(ranked: List[str], rel: set, k: int = 10) -> float:
    return sum(1 for d in ranked[:k] if d in rel) / len(rel) if rel else 0.0

def mrr_score(ranked: List[str], rel: set) -> float:
    for i, d in enumerate(ranked, 1):
        if d in rel: return 1.0 / i
    return 0.0


def evaluate(method_id: str, method_name: str, search_fn, queries: List[Dict], k: int = 10) -> Dict:
    ndcgs, recalls, mrrs, lats = [], [], [], []
    for q in queries:
        rel = set(str(r) for r in q.get("relevant_ids", []))
        if not rel: continue
        t0 = time.perf_counter()
        ranked = search_fn(q["text"])
        lats.append((time.perf_counter() - t0) * 1000)
        ndcgs.append(ndcg_at_k([str(r) for r in ranked], rel, k))
        recalls.append(recall_at_k([str(r) for r in ranked], rel, k))
        mrrs.append(mrr_score([str(r) for r in ranked], rel))

    def mean(xs): return round(sum(xs) / len(xs), 4) if xs else 0.0
    def pct(xs, p):
        s = sorted(xs); return round(s[int(len(s) * p / 100)], 2) if s else 0.0

    r = {
        "method_id": method_id, "method": method_name,
        "n_queries": len(ndcgs),
        "ndcg_at_10": mean(ndcgs), "recall_at_10": mean(recalls), "mrr": mean(mrrs),
        "latency_p50_ms": pct(lats, 50), "latency_p95_ms": pct(lats, 95),
    }
    print(f"    NDCG@10={r['ndcg_at_10']:.4f}  R@10={r['recall_at_10']:.4f}"
          f"  MRR={r['mrr']:.4f}  p50={r['latency_p50_ms']:.1f}ms")
    return r


# ---------------------------------------------------------------------------
# BGE-M3 helpers
# ---------------------------------------------------------------------------

def load_bgem3():
    from FlagEmbedding import BGEM3FlagModel
    print("  Loading BGE-M3 model...", end="", flush=True)
    t0 = time.perf_counter()
    model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True)
    print(f" done ({time.perf_counter()-t0:.1f}s)")
    return model


def encode_query_sparse(model, query_text: str) -> dict:
    out = model.encode([query_text], return_sparse=True, return_dense=False, return_colbert_vecs=False)
    return out["lexical_weights"][0]


# ---------------------------------------------------------------------------
# Hybrid helpers
# ---------------------------------------------------------------------------

def rrf_merge(ranked_lists: List[List[str]], k: int = 60) -> List[str]:
    """Reciprocal Rank Fusion."""
    scores: Dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, doc_id in enumerate(ranked):
            scores[str(doc_id)] = scores.get(str(doc_id), 0.0) + 1.0 / (k + rank + 1)
    return [doc_id for doc_id, _ in sorted(scores.items(), key=lambda x: -x[1])]


def bayesian_fuse(scores_a: np.ndarray, scores_b: np.ndarray,
                  transform_a=None, transform_b=None) -> np.ndarray:
    """Fuse two score arrays using Bayesian log-odds conjunction.

    Both score arrays are converted to probabilities, then combined with
    balanced_log_odds_fusion (min-max normalised in logit space, equal weight).

    transform_a / transform_b: callable(scores) -> probs in (0,1).
    If None, uses BayesianProbabilityTransform.likelihood() (sigmoid).
    """
    from bayesian_bm25 import BayesianProbabilityTransform, balanced_log_odds_fusion, cosine_to_probability

    calib = BayesianProbabilityTransform(alpha=0.5, beta=0.0)

    if transform_a is None:
        probs_a = calib.likelihood(np.asarray(scores_a, dtype=np.float64))
    else:
        probs_a = transform_a(scores_a)

    if transform_b is None:
        probs_b = calib.likelihood(np.asarray(scores_b, dtype=np.float64))
    else:
        probs_b = transform_b(scores_b)

    # balanced_log_odds_fusion expects (sparse_probs, dense_similarities)
    # We pass both as raw probs; for the second arg we "undo" the cosine_to_prob mapping
    # by feeding it as probs_b directly after re-mapping to [-1, 1] via inverse.
    # Simpler: use log_odds_conjunction directly.
    from scipy.special import logit as _logit
    from scipy.special import expit as _sigmoid

    def _clamp(x): return np.clip(x, 1e-9, 1 - 1e-9)

    la = _logit(_clamp(probs_a))
    lb = _logit(_clamp(probs_b))

    # Min-max normalise each into [0,1] logit space, then average
    def _norm(x):
        lo, hi = x.min(), x.max()
        return (x - lo) / (hi - lo + 1e-12)

    fused_logit = 0.5 * _norm(la) + 0.5 * _norm(lb)
    return fused_logit  # higher = more relevant (not a prob, used for ranking only)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 4: BM25 vs neural sparse + hybrid")
    parser.add_argument("--db-url", default="postgresql://postgres:postgres@localhost:5432/dev")
    parser.add_argument("--miracl-queries", default="data/miracl/queries_dev.json")
    parser.add_argument("--ezis-chunks",    default="data/ezis/chunks.json")
    parser.add_argument("--ezis-queries",   default="data/ezis/queries.json")
    parser.add_argument("--output-dir",     default="results/phase4")
    parser.add_argument("--k", type=int,    default=10)
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    # Load existing results (resume support)
    existing_path = os.path.join(args.output_dir, "phase4_comparison.json")
    if os.path.exists(existing_path):
        all_results = json.loads(Path(existing_path).read_text())
        # Migrate old method IDs to new naming scheme
        ID_RENAMES = {"4-bgem3": "4-bgem3-sparse", "4-ezis-bgem3": "4-ezis-bgem3-dense"}
        for r in all_results.get("miracl", []) + all_results.get("ezis", []):
            if r["method_id"] in ID_RENAMES:
                r["method_id"] = ID_RENAMES[r["method_id"]]
        done_miracl = {r["method_id"] for r in all_results.get("miracl", [])}
        done_ezis   = {r["method_id"] for r in all_results.get("ezis", [])}
        print(f"[Phase 4] Loaded existing results: MIRACL={done_miracl}, EZIS={done_ezis}")
    else:
        all_results: Dict[str, List] = {"miracl": [], "ezis": []}
        done_miracl: set = set()
        done_ezis: set   = set()

    print("[Phase 4] Loading queries...")
    miracl_queries = [{"text": q["text"],
                        "relevant_ids": [str(r) for r in q.get("relevant_ids", [])]}
                      for q in json.load(open(args.miracl_queries))]
    ezis_docs    = [{"id": str(c["id"]), "text": c["text"]}
                    for c in json.load(open(args.ezis_chunks))]
    ezis_queries = [{"text": q["text"],
                      "relevant_ids": [str(r) for r in q.get("relevant_ids", [])]}
                    for q in json.load(open(args.ezis_queries))]

    print(f"  MIRACL: {len(miracl_queries)} queries | EZIS: {len(ezis_docs)} docs, {len(ezis_queries)} queries")

    conn = psycopg2.connect(args.db_url)
    register_vector(conn)

    # Load BGE-M3 document sparse vectors (stored as JSON text in DB)
    print("  Loading BGE-M3 doc sparse vectors from DB...")
    with conn.cursor() as cur:
        cur.execute("SELECT doc_id, sparse_vec FROM neural_sparse_vectors "
                    "WHERE encoder_name='bge-m3-sparse' ORDER BY doc_id")
        bgem3_rows = cur.fetchall()
    bgem3_doc_ids = [str(r[0]) for r in bgem3_rows]
    bgem3_doc_vecs = [json.loads(r[1]) for r in bgem3_rows]
    print(f"  Loaded {len(bgem3_doc_vecs)} BGE-M3 sparse vectors")

    # Load BM25 kiwi-cong embedder (Phase 3 best)
    print("\n[Setup] Loading BM25 kiwi-cong embedder (Phase 3 best)...")
    from experiments.common.bm25_module import BM25Embedder_PG, BM25Embedder
    BM25_TABLE  = "text_embedding_sparse_bm25_kiwi_cong"
    VOCAB_CACHE = ".cache/bm25_vocab_kiwi_cong.json"

    bm25_emb = BM25Embedder_PG(tokenizer="kiwi-cong")
    if bm25_emb.load_vocab(VOCAB_CACHE):
        print(f"  Loaded vocab from cache: vocab_size={bm25_emb.vocab_size}")
    else:
        with conn.cursor() as cur:
            cur.execute("SELECT id::text, text FROM text_embedding ORDER BY id")
            rows = cur.fetchall()
        corpus_texts_for_fit = [r[1] for r in rows]
        print(f"  Fitting on {len(corpus_texts_for_fit)} docs...", end="", flush=True)
        t0 = time.perf_counter()
        bm25_emb.fit(corpus_texts_for_fit)
        print(f" done ({time.perf_counter()-t0:.1f}s), vocab={bm25_emb.vocab_size}")
        bm25_emb.save_vocab(VOCAB_CACHE)
        print(f"  Saved vocab cache -> {VOCAB_CACHE}")

    # Load BGE-M3 model
    print("\n[Setup] Loading BGE-M3...")
    bgem3 = load_bgem3()

    # Load MIRACL corpus from DB (needed for dense experiments)
    print("\n[Setup] Loading MIRACL corpus from DB...")
    with conn.cursor() as cur:
        cur.execute("SELECT id::text, text FROM text_embedding ORDER BY id")
        rows = cur.fetchall()
    corpus_ids   = [r[0] for r in rows]
    corpus_texts = [r[1] for r in rows]
    print(f"  {len(corpus_ids)} docs loaded")

    # Encode corpus with BGE-M3 dense (needed for dense experiments)
    corpus_dense: Optional[np.ndarray] = None
    needs_miracl_dense = not (
        {"4-bgem3-dense", "4-hybrid-rrf-dense", "4-bayes-dense"} <= done_miracl
    )
    if needs_miracl_dense:
        print("\n[Setup] Encoding MIRACL corpus with BGE-M3 dense (slow, batched)...")
        t0 = time.perf_counter()
        dense_out = bgem3.encode(corpus_texts, return_dense=True, return_sparse=False,
                                  return_colbert_vecs=False, batch_size=256)
        corpus_dense = dense_out["dense_vecs"]
        print(f"  Done in {time.perf_counter()-t0:.1f}s. Shape: {corpus_dense.shape}")

    k = args.k

    # -----------------------------------------------------------------------
    # Helper: BM25 top-N from pgvector WITH scores
    # Returns [(doc_id_str, bm25_score), ...]
    # pgvector <#> = negative inner product, so score = -distance
    # -----------------------------------------------------------------------
    def bm25_topn_with_scores(q: str, n: int):
        qv = bm25_emb.embed_query(q)
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id::text, -(emb_sparse <#> %s::sparsevec) AS score "
                f"FROM {BM25_TABLE} ORDER BY emb_sparse <#> %s::sparsevec LIMIT {n}",
                (qv, qv),
            )
            return [(str(r[0]), float(r[1])) for r in cur.fetchall()]

    def bm25_topn_ids(q: str, n: int):
        return [doc_id for doc_id, _ in bm25_topn_with_scores(q, n)]

    # -----------------------------------------------------------------------
    # MIRACL — BM25 standalone
    # -----------------------------------------------------------------------

    if "4-bm25-kiwi" not in done_miracl:
        print("\n[MIRACL] Method 4-bm25-kiwi: BM25 (kiwi-cong) pgvector-sparse...")
        def bm25_search(q):
            return bm25_topn_ids(q, k)
        r = evaluate("4-bm25-kiwi", "BM25 kiwi-cong (pgvector)", bm25_search, miracl_queries, k)
        r["dataset"] = "miracl"; all_results["miracl"].append(r)

    # -----------------------------------------------------------------------
    # MIRACL — BGE-M3 sparse standalone
    # -----------------------------------------------------------------------

    if "4-bgem3-sparse" not in done_miracl:
        print("\n[MIRACL] Method 4-bgem3-sparse: BGE-M3 sparse (Python dot product)...")
        def bgem3_sparse_search(q):
            q_weights = encode_query_sparse(bgem3, q)
            scored = []
            for doc_id, doc_vec in zip(bgem3_doc_ids, bgem3_doc_vecs):
                score = sum(float(q_weights.get(tok, 0)) * float(v) for tok, v in doc_vec.items())
                scored.append((score, doc_id))
            scored.sort(key=lambda x: -x[0])
            return [doc_id for _, doc_id in scored[:k]]
        r = evaluate("4-bgem3-sparse", "BGE-M3 sparse (neural)", bgem3_sparse_search, miracl_queries, k)
        r["dataset"] = "miracl"; all_results["miracl"].append(r)
    else:
        def bgem3_sparse_search(q):
            q_weights = encode_query_sparse(bgem3, q)
            scored = []
            for doc_id, doc_vec in zip(bgem3_doc_ids, bgem3_doc_vecs):
                score = sum(float(q_weights.get(tok, 0)) * float(v) for tok, v in doc_vec.items())
                scored.append((score, doc_id))
            scored.sort(key=lambda x: -x[0])
            return [doc_id for _, doc_id in scored[:k]]

    # Backward-compat alias for old method_id 4-bgem3
    if "4-bgem3" in done_miracl and "4-bgem3-sparse" not in done_miracl:
        # rename existing entry
        for r in all_results["miracl"]:
            if r["method_id"] == "4-bgem3":
                r["method_id"] = "4-bgem3-sparse"
                done_miracl.add("4-bgem3-sparse")
                print("  Renamed 4-bgem3 -> 4-bgem3-sparse in MIRACL results")
                break

    # -----------------------------------------------------------------------
    # MIRACL — BGE-M3 dense standalone
    # -----------------------------------------------------------------------

    if "4-bgem3-dense" not in done_miracl:
        print("\n[MIRACL] Method 4-bgem3-dense: BGE-M3 dense (cosine, batch numpy)...")
        def bgem3_dense_search(q):
            q_out = bgem3.encode([q], return_dense=True, return_sparse=False, return_colbert_vecs=False)
            q_vec = q_out["dense_vecs"][0]
            scores = corpus_dense @ q_vec  # L2-normalised -> dot product == cosine
            top_k_idx = np.argpartition(scores, -k)[-k:]
            top_k_idx = top_k_idx[np.argsort(scores[top_k_idx])[::-1]]
            return [corpus_ids[i] for i in top_k_idx]
        r = evaluate("4-bgem3-dense", "BGE-M3 dense (cosine)", bgem3_dense_search, miracl_queries, k)
        r["dataset"] = "miracl"; all_results["miracl"].append(r)
    else:
        def bgem3_dense_search(q):
            q_out = bgem3.encode([q], return_dense=True, return_sparse=False, return_colbert_vecs=False)
            q_vec = q_out["dense_vecs"][0]
            scores = corpus_dense @ q_vec
            top_k_idx = np.argpartition(scores, -k)[-k:]
            top_k_idx = top_k_idx[np.argsort(scores[top_k_idx])[::-1]]
            return [corpus_ids[i] for i in top_k_idx]

    # -----------------------------------------------------------------------
    # MIRACL — BM25 + sparse RRF
    # -----------------------------------------------------------------------

    if "4-hybrid-rrf" not in done_miracl:
        print("\n[MIRACL] Method 4-hybrid-rrf: Hybrid BM25+BGE-M3 sparse (RRF k=60)...")
        K_CAND = 100
        def hybrid_rrf_search(q):
            bm25_top  = bm25_topn_ids(q, K_CAND)
            q_weights = encode_query_sparse(bgem3, q)
            sparse_scored = [(sum(float(q_weights.get(tok, 0)) * float(v)
                                  for tok, v in dv.items()), did)
                             for did, dv in zip(bgem3_doc_ids, bgem3_doc_vecs)]
            sparse_scored.sort(key=lambda x: -x[0])
            sparse_top = [did for _, did in sparse_scored[:K_CAND]]
            return rrf_merge([bm25_top, sparse_top])[:k]
        r = evaluate("4-hybrid-rrf", "Hybrid BM25+BGE-M3 sparse (RRF)", hybrid_rrf_search, miracl_queries, k)
        r["dataset"] = "miracl"; all_results["miracl"].append(r)

    # -----------------------------------------------------------------------
    # MIRACL — BM25 + dense RRF
    # -----------------------------------------------------------------------

    if "4-hybrid-rrf-dense" not in done_miracl and corpus_dense is not None:
        print("\n[MIRACL] Method 4-hybrid-rrf-dense: Hybrid BM25+BGE-M3 dense (RRF k=60)...")
        K_CAND_D = 100
        def hybrid_rrf_dense_search(q):
            bm25_top = bm25_topn_ids(q, K_CAND_D)
            q_out = bgem3.encode([q], return_dense=True, return_sparse=False, return_colbert_vecs=False)
            q_vec = q_out["dense_vecs"][0]
            scores = corpus_dense @ q_vec
            top_k_idx = np.argpartition(scores, -K_CAND_D)[-K_CAND_D:]
            top_k_idx = top_k_idx[np.argsort(scores[top_k_idx])[::-1]]
            dense_top = [corpus_ids[i] for i in top_k_idx]
            return rrf_merge([bm25_top, dense_top])[:k]
        r = evaluate("4-hybrid-rrf-dense", "Hybrid BM25+BGE-M3 dense (RRF)", hybrid_rrf_dense_search, miracl_queries, k)
        r["dataset"] = "miracl"; all_results["miracl"].append(r)

    # -----------------------------------------------------------------------
    # MIRACL — BM25 + sparse Bayesian fusion
    # -----------------------------------------------------------------------

    if "4-bayes-sparse" not in done_miracl:
        print("\n[MIRACL] Method 4-bayes-sparse: Bayesian BM25+BGE-M3 sparse fusion...")
        K_BAY = 200
        def bayes_sparse_search(q):
            # BM25 top-200 with scores
            bm25_cands = bm25_topn_with_scores(q, K_BAY)   # [(id, score)]
            bm25_id_set = {did for did, _ in bm25_cands}

            # BGE-M3 sparse scores for all docs, get top-200
            q_weights = encode_query_sparse(bgem3, q)
            sparse_all = [(sum(float(q_weights.get(tok, 0)) * float(v)
                               for tok, v in dv.items()), did)
                          for did, dv in zip(bgem3_doc_ids, bgem3_doc_vecs)]
            sparse_all.sort(key=lambda x: -x[0])
            sparse_cands_top = sparse_all[:K_BAY]
            sparse_id_set = {did for _, did in sparse_cands_top}

            # Union of candidates
            all_cand_ids = list(bm25_id_set | sparse_id_set)

            # Build lookup dicts
            bm25_lookup   = {did: sc for did, sc in bm25_cands}
            sparse_lookup = {did: sc for sc, did in sparse_all}

            bm25_min  = min(bm25_lookup.values())  if bm25_lookup  else 0.0
            sparse_min = min(sparse_lookup.values()) if sparse_lookup else 0.0

            bm25_scores_arr  = np.array([bm25_lookup.get(cid, bm25_min)   for cid in all_cand_ids])
            sparse_scores_arr = np.array([sparse_lookup.get(cid, sparse_min) for cid in all_cand_ids])

            fused = bayesian_fuse(bm25_scores_arr, sparse_scores_arr)
            order = np.argsort(-fused)
            return [all_cand_ids[i] for i in order[:k]]
        r = evaluate("4-bayes-sparse", "Bayesian BM25+BGE-M3 sparse", bayes_sparse_search, miracl_queries, k)
        r["dataset"] = "miracl"; all_results["miracl"].append(r)

    # -----------------------------------------------------------------------
    # MIRACL — BM25 + dense Bayesian fusion
    # -----------------------------------------------------------------------

    if "4-bayes-dense" not in done_miracl and corpus_dense is not None:
        print("\n[MIRACL] Method 4-bayes-dense: Bayesian BM25+BGE-M3 dense fusion...")
        K_BAY_D = 200
        def bayes_dense_search(q):
            # BM25 top-200 with scores
            bm25_cands = bm25_topn_with_scores(q, K_BAY_D)
            bm25_lookup = {did: sc for did, sc in bm25_cands}

            # Dense cosine similarities for ALL corpus docs
            q_out = bgem3.encode([q], return_dense=True, return_sparse=False, return_colbert_vecs=False)
            q_vec = q_out["dense_vecs"][0]
            dense_all = corpus_dense @ q_vec   # shape (N,)

            # Dense top-200
            dense_top_idx = np.argpartition(dense_all, -K_BAY_D)[-K_BAY_D:]
            dense_id_set  = {corpus_ids[i] for i in dense_top_idx}

            # Union
            all_cand_ids = list({did for did in bm25_lookup} | dense_id_set)

            bm25_min = min(bm25_lookup.values()) if bm25_lookup else 0.0
            bm25_scores_arr  = np.array([bm25_lookup.get(cid, bm25_min) for cid in all_cand_ids])
            # dense scores indexed by position in corpus_ids
            corp_idx = {cid: i for i, cid in enumerate(corpus_ids)}
            dense_scores_arr = np.array([float(dense_all[corp_idx[cid]]) for cid in all_cand_ids])

            fused = bayesian_fuse(bm25_scores_arr, dense_scores_arr)
            order = np.argsort(-fused)
            return [all_cand_ids[i] for i in order[:k]]
        r = evaluate("4-bayes-dense", "Bayesian BM25+BGE-M3 dense", bayes_dense_search, miracl_queries, k)
        r["dataset"] = "miracl"; all_results["miracl"].append(r)

    # Save after MIRACL
    out_path = os.path.join(args.output_dir, "phase4_comparison.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\n[Phase 4] MIRACL checkpoint saved: {out_path}")

    # -----------------------------------------------------------------------
    # EZIS — in-memory BM25 + dense + hybrid
    # -----------------------------------------------------------------------

    ezis_texts = [d["text"] for d in ezis_docs]
    ezis_ids   = [d["id"]   for d in ezis_docs]

    # In-memory BM25 (97 docs)
    emb_ez = BM25Embedder(tokenizer="kiwi-cong")
    emb_ez.fit(ezis_texts)
    ez_doc_vecs = [emb_ez.embed_document(t) for t in ezis_texts]

    def ezis_bm25_scores(q) -> List[tuple]:
        """Returns [(doc_id, bm25_score)] for all EZIS docs."""
        qv = emb_ez.embed_query(q)
        if not qv: return [(did, 0.0) for did in ezis_ids]
        return [(did, sum(qv.get(tok, 0.0) * w for tok, w in dv.items()))
                for did, dv in zip(ezis_ids, ez_doc_vecs)]

    if "4-ezis-bm25" not in done_ezis:
        print("\n[EZIS] Method 4-ezis-bm25: BM25 (kiwi-cong) in-memory...")
        def ezis_bm25_search(q):
            return [did for did, _ in sorted(ezis_bm25_scores(q), key=lambda x: -x[1])[:k]]
        r = evaluate("4-ezis-bm25", "BM25 kiwi-cong (in-memory)", ezis_bm25_search, ezis_queries, k)
        r["dataset"] = "ezis"; all_results["ezis"].append(r)

    # EZIS BGE-M3 dense (encode 97 docs once)
    print("\n[EZIS] Encoding EZIS docs with BGE-M3 dense...", end="", flush=True)
    t0 = time.perf_counter()
    ez_out = bgem3.encode(ezis_texts, return_dense=True, return_sparse=False, return_colbert_vecs=False)
    ez_dense = ez_out["dense_vecs"]
    print(f" done ({time.perf_counter()-t0:.1f}s)")

    if "4-ezis-bgem3-dense" not in done_ezis:
        print("\n[EZIS] Method 4-ezis-bgem3-dense: BGE-M3 dense semantic search...")
        def ezis_bgem3_dense_search(q):
            q_out = bgem3.encode([q], return_dense=True, return_sparse=False, return_colbert_vecs=False)
            q_vec = q_out["dense_vecs"][0]
            scores = ez_dense @ q_vec   # L2-normalised
            order  = np.argsort(-scores)
            return [ezis_ids[i] for i in order[:k]]
        r = evaluate("4-ezis-bgem3-dense", "BGE-M3 dense (cosine)", ezis_bgem3_dense_search, ezis_queries, k)
        r["dataset"] = "ezis"; all_results["ezis"].append(r)
    else:
        # old method_id was 4-ezis-bgem3 — rename silently
        for row in all_results["ezis"]:
            if row["method_id"] == "4-ezis-bgem3":
                row["method_id"] = "4-ezis-bgem3-dense"
                done_ezis.add("4-ezis-bgem3-dense")
                print("  Renamed 4-ezis-bgem3 -> 4-ezis-bgem3-dense in EZIS results")
                break

        def ezis_bgem3_dense_search(q):
            q_out = bgem3.encode([q], return_dense=True, return_sparse=False, return_colbert_vecs=False)
            q_vec = q_out["dense_vecs"][0]
            scores = ez_dense @ q_vec
            order  = np.argsort(-scores)
            return [ezis_ids[i] for i in order[:k]]

    # EZIS BGE-M3 sparse (encode 97 docs once)
    print("\n[EZIS] Encoding EZIS docs with BGE-M3 sparse...", end="", flush=True)
    t0 = time.perf_counter()
    ez_sparse_out = bgem3.encode(ezis_texts, return_sparse=True, return_dense=False, return_colbert_vecs=False)
    ez_sparse_vecs = ez_sparse_out["lexical_weights"]
    print(f" done ({time.perf_counter()-t0:.1f}s)")

    if "4-ezis-bgem3-sparse" not in done_ezis:
        print("\n[EZIS] Method 4-ezis-bgem3-sparse: BGE-M3 sparse (dot product)...")
        def ezis_bgem3_sparse_search(q):
            q_weights = encode_query_sparse(bgem3, q)
            scored = [(sum(float(q_weights.get(tok, 0)) * float(v) for tok, v in sv.items()), did)
                      for did, sv in zip(ezis_ids, ez_sparse_vecs)]
            scored.sort(key=lambda x: -x[0])
            return [did for _, did in scored[:k]]
        r = evaluate("4-ezis-bgem3-sparse", "BGE-M3 sparse (neural)", ezis_bgem3_sparse_search, ezis_queries, k)
        r["dataset"] = "ezis"; all_results["ezis"].append(r)
    else:
        def ezis_bgem3_sparse_search(q):
            q_weights = encode_query_sparse(bgem3, q)
            scored = [(sum(float(q_weights.get(tok, 0)) * float(v) for tok, v in sv.items()), did)
                      for did, sv in zip(ezis_ids, ez_sparse_vecs)]
            scored.sort(key=lambda x: -x[0])
            return [did for _, did in scored[:k]]

    # -----------------------------------------------------------------------
    # EZIS — BM25 + sparse RRF
    # -----------------------------------------------------------------------

    if "4-ezis-hybrid-rrf" not in done_ezis:
        print("\n[EZIS] Method 4-ezis-hybrid-rrf: Hybrid BM25+BGE-M3 sparse (RRF)...")
        def ezis_hybrid_rrf_search(q):
            bm25_top   = [did for did, _ in sorted(ezis_bm25_scores(q), key=lambda x: -x[1])]
            q_weights  = encode_query_sparse(bgem3, q)
            sparse_top = [did for _, did in sorted(
                [(sum(float(q_weights.get(tok, 0)) * float(v) for tok, v in sv.items()), did)
                 for did, sv in zip(ezis_ids, ez_sparse_vecs)],
                key=lambda x: -x[0])]
            return rrf_merge([bm25_top, sparse_top])[:k]
        r = evaluate("4-ezis-hybrid-rrf", "Hybrid BM25+BGE-M3 sparse (RRF)", ezis_hybrid_rrf_search, ezis_queries, k)
        r["dataset"] = "ezis"; all_results["ezis"].append(r)

    # -----------------------------------------------------------------------
    # EZIS — BM25 + dense RRF
    # -----------------------------------------------------------------------

    if "4-ezis-hybrid-rrf-dense" not in done_ezis:
        print("\n[EZIS] Method 4-ezis-hybrid-rrf-dense: Hybrid BM25+BGE-M3 dense (RRF)...")
        def ezis_hybrid_rrf_dense_search(q):
            bm25_top  = [did for did, _ in sorted(ezis_bm25_scores(q), key=lambda x: -x[1])]
            q_out = bgem3.encode([q], return_dense=True, return_sparse=False, return_colbert_vecs=False)
            q_vec = q_out["dense_vecs"][0]
            scores = ez_dense @ q_vec
            dense_top = [ezis_ids[i] for i in np.argsort(-scores)]
            return rrf_merge([bm25_top, dense_top])[:k]
        r = evaluate("4-ezis-hybrid-rrf-dense", "Hybrid BM25+BGE-M3 dense (RRF)", ezis_hybrid_rrf_dense_search, ezis_queries, k)
        r["dataset"] = "ezis"; all_results["ezis"].append(r)

    # -----------------------------------------------------------------------
    # EZIS — BM25 + sparse Bayesian fusion
    # -----------------------------------------------------------------------

    if "4-ezis-bayes-sparse" not in done_ezis:
        print("\n[EZIS] Method 4-ezis-bayes-sparse: Bayesian BM25+BGE-M3 sparse fusion...")
        def ezis_bayes_sparse_search(q):
            bm25_scores_all = ezis_bm25_scores(q)   # [(id, score)]
            q_weights = encode_query_sparse(bgem3, q)
            sparse_scores_all = [(did, sum(float(q_weights.get(tok, 0)) * float(v) for tok, v in sv.items()))
                                 for did, sv in zip(ezis_ids, ez_sparse_vecs)]

            ids  = [x[0] for x in bm25_scores_all]
            bm25_arr   = np.array([x[1] for x in bm25_scores_all])
            sparse_dict = {did: sc for did, sc in sparse_scores_all}
            sparse_arr  = np.array([sparse_dict[did] for did in ids])

            fused = bayesian_fuse(bm25_arr, sparse_arr)
            order = np.argsort(-fused)
            return [ids[i] for i in order[:k]]
        r = evaluate("4-ezis-bayes-sparse", "Bayesian BM25+BGE-M3 sparse", ezis_bayes_sparse_search, ezis_queries, k)
        r["dataset"] = "ezis"; all_results["ezis"].append(r)

    # -----------------------------------------------------------------------
    # EZIS — BM25 + dense Bayesian fusion
    # -----------------------------------------------------------------------

    if "4-ezis-bayes-dense" not in done_ezis:
        print("\n[EZIS] Method 4-ezis-bayes-dense: Bayesian BM25+BGE-M3 dense fusion...")
        def ezis_bayes_dense_search(q):
            bm25_scores_all = ezis_bm25_scores(q)   # [(id, score)]
            q_out = bgem3.encode([q], return_dense=True, return_sparse=False, return_colbert_vecs=False)
            q_vec = q_out["dense_vecs"][0]
            dense_sims = ez_dense @ q_vec   # shape (97,)

            ids       = [x[0] for x in bm25_scores_all]
            bm25_arr  = np.array([x[1] for x in bm25_scores_all])
            dense_arr = np.array([float(dense_sims[i]) for i in range(len(ids))])

            fused = bayesian_fuse(bm25_arr, dense_arr)
            order = np.argsort(-fused)
            return [ids[i] for i in order[:k]]
        r = evaluate("4-ezis-bayes-dense", "Bayesian BM25+BGE-M3 dense", ezis_bayes_dense_search, ezis_queries, k)
        r["dataset"] = "ezis"; all_results["ezis"].append(r)

    conn.close()

    # Final save
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\n[Phase 4] Saved: {out_path}")

    print("\n" + "=" * 70)
    for ds in ["miracl", "ezis"]:
        print(f"\n{ds.upper()} results:")
        for r in sorted(all_results[ds], key=lambda x: -x["ndcg_at_10"]):
            print(f"  {r['method_id']:28} {r['method']:45} NDCG@10={r['ndcg_at_10']:.4f}")


if __name__ == "__main__":
    main()
