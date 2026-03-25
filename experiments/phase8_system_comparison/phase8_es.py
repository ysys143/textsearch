"""
Phase 8: Elasticsearch Benchmark

Infrastructure: Elasticsearch 8.x + nori analyzer (Korean morphological)
                + dense_vector (BGE-M3 1024-dim) + built-in RRF hybrid

Methods:
  - BM25    : nori analyzer, multi_match
  - Dense   : knn query (BGE-M3, retrieval-only*)
  - Hybrid  : ES RRF (knn + multi_match, rank_window_size=60)

*retrieval-only: BGE-M3 inference excluded, query embeddings pre-computed

Datasets:
  - MIRACL-ko 10K (213 queries)
  - EZIS       97 (131 queries)

Metrics: NDCG@10, Recall@10, MRR, latency p50/p95/p99

Usage:
  # Start ES first:
  #   docker compose --profile phase8-es up -d
  uv run python3 experiments/phase8_system_comparison/phase8_es.py \\
    --es-url http://localhost:9200 \\
    --output-dir results/phase8
"""

import argparse
import json
import math
import os
import time
from datetime import datetime
from typing import Dict, List, Optional

from elasticsearch import Elasticsearch
from elasticsearch.helpers import bulk

MIRACL_QUERIES_PATH = "data/miracl/queries_dev.json"
EZIS_QUERIES_PATH   = "data/ezis/queries.json"
EMB_MIRACL_PATH     = "data/phase8/query_embs_miracl.json"
EMB_EZIS_PATH       = "data/phase8/query_embs_ezis.json"
N_MIRACL = 213
TOPK     = 60
WARMUP   = 5

NORI_STOPTAGS = [
    "E", "IC", "J", "MAG", "MM", "SP", "SSC", "SSO",
    "SC", "SE", "XPN", "XSA", "XSN", "XSV", "UNA", "NA", "VSV",
]

INDEX_SETTINGS = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "analysis": {
            "analyzer": {
                "korean": {
                    "type": "nori",
                    "decompound_mode": "mixed",
                    "stoptags": NORI_STOPTAGS,
                }
            }
        },
    },
    "mappings": {
        "properties": {
            "id":        {"type": "keyword"},
            "text":      {"type": "text", "analyzer": "korean"},
            "dense_vec": {
                "type": "dense_vector",
                "dims": 1024,
                "index": True,
                "similarity": "cosine",
            },
        }
    },
}


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
# Index setup
# ---------------------------------------------------------------------------

def setup_index(es: Elasticsearch, index_name: str, docs: List[dict],
                embeddings: Dict[str, List[float]]) -> float:
    if es.indices.exists(index=index_name):
        es.indices.delete(index=index_name)
    es.indices.create(index=index_name, body=INDEX_SETTINGS)

    def gen_actions():
        for doc in docs:
            action = {
                "_index": index_name,
                "_id": doc["id"],
                "_source": {
                    "id": doc["id"],
                    "text": doc["text"],
                    "dense_vec": embeddings.get(doc["id"], [0.0] * 1024),
                },
            }
            yield action

    t0 = time.perf_counter()
    bulk(es, gen_actions(), chunk_size=500, request_timeout=300)
    es.indices.refresh(index=index_name)
    return round(time.perf_counter() - t0, 2)


# ---------------------------------------------------------------------------
# Search functions
# ---------------------------------------------------------------------------

def search_bm25(es: Elasticsearch, index_name: str,
                query_text: str, k: int = 10) -> List[str]:
    resp = es.search(
        index=index_name,
        body={
            "query": {"match": {"text": {"query": query_text, "analyzer": "korean"}}},
            "size": k,
            "_source": False,
        }
    )
    return [hit["_id"] for hit in resp["hits"]["hits"]]


def search_dense(es: Elasticsearch, index_name: str,
                 query_emb: List[float], k: int = 10) -> List[str]:
    resp = es.search(
        index=index_name,
        body={
            "knn": {
                "field": "dense_vec",
                "query_vector": query_emb,
                "k": k,
                "num_candidates": TOPK,
            },
            "size": k,
            "_source": False,
        }
    )
    return [hit["_id"] for hit in resp["hits"]["hits"]]


def search_hybrid(es: Elasticsearch, index_name: str,
                  query_text: str, query_emb: List[float], k: int = 10) -> List[str]:
    resp = es.search(
        index=index_name,
        body={
            "retriever": {
                "rrf": {
                    "retrievers": [
                        {
                            "standard": {
                                "query": {
                                    "match": {
                                        "text": {
                                            "query": query_text,
                                            "analyzer": "korean",
                                        }
                                    }
                                }
                            }
                        },
                        {
                            "knn": {
                                "field": "dense_vec",
                                "query_vector": query_emb,
                                "k": TOPK,
                                "num_candidates": TOPK,
                            }
                        },
                    ],
                    "rank_window_size": TOPK,
                    "rank_constant": 60,
                }
            },
            "size": k,
            "_source": False,
        }
    )
    return [hit["_id"] for hit in resp["hits"]["hits"]]


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

def bench_dataset(es: Elasticsearch, dataset_name: str,
                  queries: List[dict], query_embs: Dict[str, List[float]],
                  index_name: str) -> List[dict]:
    print(f"\n  === {dataset_name} | index={index_name} | {len(queries)} queries ===")

    methods = [
        ("BM25",   lambda t, e: search_bm25(es, index_name, t)),
        ("Dense",  lambda t, e: search_dense(es, index_name, e) if e else []),
        ("Hybrid", lambda t, e: search_hybrid(es, index_name, t, e) if e else []),
    ]

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
            "system": "elasticsearch",
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
        print(f"  [{name:8s}] NDCG@10={r['ndcg_at_10']}  "
              f"Recall={r['recall_at_10']}  MRR={r['mrr']}  "
              f"p50={r['latency_p50']}ms  p95={r['latency_p95']}ms")
        results.append(r)

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--es-url", default="http://localhost:9200")
    parser.add_argument("--output-dir", default="results/phase8")
    args = parser.parse_args()

    print("=" * 60)
    print("Phase 8: Elasticsearch Benchmark")
    print("=" * 60)

    es = Elasticsearch(args.es_url, request_timeout=60)
    print(f"  ES version: {es.info()['version']['number']}")

    # Load data
    miracl_queries = load_queries(MIRACL_QUERIES_PATH, limit=N_MIRACL)
    ezis_queries   = load_queries(EZIS_QUERIES_PATH)
    miracl_embs    = load_embeddings(EMB_MIRACL_PATH)
    ezis_embs      = load_embeddings(EMB_EZIS_PATH)

    # Load corpus from query data (id + text fields)
    miracl_docs = [{"id": q["query_id"], "text": q["text"]} for q in
                   load_queries(MIRACL_QUERIES_PATH)]
    # Load actual corpus docs separately if available
    miracl_corpus_path = "data/miracl/corpus_10k.json"
    if os.path.exists(miracl_corpus_path):
        with open(miracl_corpus_path, encoding="utf-8") as f:
            miracl_docs = json.load(f)
    print(f"  MIRACL corpus: {len(miracl_docs)} docs, {len(miracl_queries)} queries")

    ezis_corpus_path = "data/ezis/corpus.json"
    ezis_docs = []
    if os.path.exists(ezis_corpus_path):
        with open(ezis_corpus_path, encoding="utf-8") as f:
            ezis_docs = json.load(f)
    print(f"  EZIS corpus: {len(ezis_docs)} docs, {len(ezis_queries)} queries")

    all_results = []

    # MIRACL
    print("\n--- Setting up MIRACL index ---")
    idx_miracl = "p8_es_miracl"
    t_build = setup_index(es, idx_miracl, miracl_docs, miracl_embs)
    print(f"  Index built in {t_build}s")
    all_results += bench_dataset(es, "MIRACL", miracl_queries, miracl_embs, idx_miracl)

    # EZIS
    if ezis_docs:
        print("\n--- Setting up EZIS index ---")
        idx_ezis = "p8_es_ezis"
        t_build = setup_index(es, idx_ezis, ezis_docs, ezis_embs)
        print(f"  Index built in {t_build}s")
        all_results += bench_dataset(es, "EZIS", ezis_queries, ezis_embs, idx_ezis)

    # Save JSON
    os.makedirs(args.output_dir, exist_ok=True)
    json_path = os.path.join(args.output_dir, "phase8_es.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "generated": datetime.now().isoformat(),
            "system": "elasticsearch",
            "results": all_results,
        }, f, indent=2, ensure_ascii=False)
    print(f"\n  JSON: {json_path}")
    print("Done.")


if __name__ == "__main__":
    main()
