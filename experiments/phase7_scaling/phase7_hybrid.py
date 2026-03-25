"""
Phase 7 Section 2: Hybrid Search Benchmark

Infrastructure: MeCab (textsearch_ko) + pg_textsearch BM25 + pgvector HNSW

Methods:
  - BM25    : pg_textsearch <@> operator (AND matching)
  - Dense   : HNSW cosine (pgvector)
  - RRF     : BM25(top-60) + Dense(top-60) rank fusion, k=60
  - Bayesian: -bm25_dist + cosine_sim 정규화 후 α*BM25 + β*Dense

Datasets:
  - MIRACL-ko 10K (p7_hybrid_miracl, 213 queries)
  - EZIS       97 (p7_hybrid_ezis,   131 queries)

Metrics: NDCG@10, Recall@10, MRR, latency p50/p95/p99

Usage:
  uv run python3 experiments/phase7_scaling/phase7_hybrid.py \\
    --db-url postgresql://postgres:postgres@localhost:5432/dev \\
    --output-dir results/phase7
"""

import argparse
import json
import math
import os
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras

MIRACL_QUERIES_PATH = "data/miracl/queries_dev.json"
EZIS_QUERIES_PATH   = "data/ezis/queries.json"
N_MIRACL = 213
RRF_K    = 60
TOPK     = 60   # candidates per component before fusion
WARMUP   = 5
BAYESIAN_ALPHA = 0.5   # weight for BM25; (1-alpha) for Dense


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


def load_query_embeddings(conn, dataset: str) -> Dict[str, List[float]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT query_id, emb FROM p7_query_emb WHERE dataset = %s",
            (dataset,)
        )
        rows = cur.fetchall()
    return {r[0]: r[1] for r in rows}


# ---------------------------------------------------------------------------
# Search functions (table/index as parameters)
# ---------------------------------------------------------------------------

def search_bm25(conn, query_text: str, table: str, bm25_idx: str,
                k: int = 10) -> List[str]:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT id FROM {table} ORDER BY text <@> %s LIMIT %s",
            (query_text, k)
        )
        return [r[0] for r in cur.fetchall()]


def search_bm25_with_scores(conn, query_text: str, table: str, bm25_idx: str,
                             k: int = TOPK) -> List[Tuple[str, float]]:
    """Returns (id, bm25_dist) — dist is negative; more negative = more relevant."""
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT id, text <@> to_bm25query(%s, %s) as bm25_dist
                FROM {table}
                ORDER BY bm25_dist LIMIT %s""",
            (query_text, bm25_idx, k)
        )
        return [(r[0], r[1]) for r in cur.fetchall()]


def search_dense(conn, query_emb: List[float], table: str,
                 k: int = 10) -> List[str]:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT id FROM {table} ORDER BY dense_vec <=> %s LIMIT %s",
            (query_emb, k)
        )
        return [r[0] for r in cur.fetchall()]


def search_dense_with_scores(conn, query_emb: List[float], table: str,
                              k: int = TOPK) -> List[Tuple[str, float]]:
    """Returns (id, cosine_dist) — dist in [0,2]; smaller = more relevant."""
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT id, dense_vec <=> %s as cosine_dist
                FROM {table}
                ORDER BY cosine_dist LIMIT %s""",
            (query_emb, k)
        )
        return [(r[0], r[1]) for r in cur.fetchall()]


def search_rrf(conn, query_text: str, query_emb: List[float],
               table: str, bm25_idx: str, k: int = 10) -> List[str]:
    bm25_ids = search_bm25(conn, query_text, table, bm25_idx, k=TOPK)
    dense_ids = search_dense(conn, query_emb, table, k=TOPK)

    scores: Dict[str, float] = {}
    for rank, doc_id in enumerate(bm25_ids, start=1):
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (RRF_K + rank)
    for rank, doc_id in enumerate(dense_ids, start=1):
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (RRF_K + rank)

    return [d for d, _ in sorted(scores.items(), key=lambda x: x[1], reverse=True)[:k]]


def search_bayesian(conn, query_text: str, query_emb: List[float],
                    table: str, bm25_idx: str, k: int = 10,
                    alpha: float = BAYESIAN_ALPHA) -> List[str]:
    """
    Single-roundtrip Bayesian fusion via registered SQL function.
    p7_bayesian_miracl / p7_bayesian_ezis: BM25 + Dense CTE, normalize+combine server-side.
    """
    fn = "p7_bayesian_miracl" if "miracl" in table else "p7_bayesian_ezis"
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT id FROM {fn}(%s, %s, %s, %s, %s)",
            (query_text, query_emb, k, TOPK, alpha)
        )
        return [r[0] for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

def bench_dataset(conn, dataset_name: str, queries: List[dict],
                  query_embs: Dict[str, List[float]],
                  table: str, bm25_idx: str) -> List[dict]:
    print(f"\n  === {dataset_name} | table={table} | {len(queries)} queries ===")

    methods = [
        ("BM25",     lambda t, e: search_bm25(conn, t, table, bm25_idx)),
        ("Dense",    lambda t, e: search_dense(conn, e, table) if e else []),
        ("RRF",      lambda t, e: search_rrf(conn, t, e, table, bm25_idx) if e else []),
        ("Bayesian", lambda t, e: search_bayesian(conn, t, e, table, bm25_idx) if e else []),
    ]

    results = []
    for name, fn in methods:
        ndcg_s, rec_s, mrr_s, lats = [], [], [], []

        # Warmup
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
        print(f"  [{name:10s}] NDCG@10={r['ndcg_at_10']}  "
              f"Recall={r['recall_at_10']}  MRR={r['mrr']}  "
              f"p50={r['latency_p50']}ms  p95={r['latency_p95']}ms")
        results.append(r)

    return results


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def generate_report(all_results: List[dict], output_dir: str) -> str:
    lines = [
        "# Phase 7 Section 2: Hybrid Search Benchmark",
        "",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "**Infrastructure:** MeCab (textsearch_ko) + pg_textsearch BM25 + pgvector HNSW",
        f"**Fusion:** RRF k={RRF_K}, Bayesian α={BAYESIAN_ALPHA} (BM25) / {1-BAYESIAN_ALPHA:.1f} (Dense)",
        "",
        "---",
        "",
    ]

    for dataset in ["MIRACL", "EZIS"]:
        rows = [r for r in all_results if r["dataset"] == dataset]
        if not rows:
            continue
        corpus = "p7_hybrid_miracl (10K)" if dataset == "MIRACL" else "p7_hybrid_ezis (97)"
        lines += [
            f"## {dataset} — {corpus}",
            "",
            "### Quality",
            "",
            "| Method | NDCG@10 | Recall@10 | MRR |",
            "|--------|---------|-----------|-----|",
        ]
        for r in rows:
            lines.append(
                f"| {r['method']} | {r['ndcg_at_10']} | {r['recall_at_10']} | {r['mrr']} |"
            )
        lines += [
            "",
            "### Latency",
            "",
            "| Method | p50 | p95 | p99 |",
            "|--------|-----|-----|-----|",
        ]
        for r in rows:
            lines.append(
                f"| {r['method']} | {r['latency_p50']}ms | {r['latency_p95']}ms | {r['latency_p99']}ms |"
            )
        lines.append("")

    lines += [
        "---",
        "",
        "## 비고",
        "",
        "- **BM25**: pg_textsearch `<@>` AND matching, MeCab 토크나이저",
        "- **Dense**: pgvector HNSW cosine (BGE-M3 1024-dim)",
        f"- **RRF**: 각 컴포넌트 top-{TOPK} 후 `1/(k+rank)` 합산 (k={RRF_K})",
        f"- **Bayesian**: `to_bm25query` 실제 BM25 스코어 + cosine sim 정규화 후 α={BAYESIAN_ALPHA}:{1-BAYESIAN_ALPHA:.1f} 결합",
        "",
    ]

    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "phase7_hybrid_report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n  Report: {path}")
    return path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-url",
                        default="postgresql://postgres:postgres@localhost:5432/dev")
    parser.add_argument("--output-dir", default="results/phase7")
    args = parser.parse_args()

    print("=" * 60)
    print("Phase 7 Section 2: Hybrid Search Benchmark")
    print("=" * 60)

    conn = psycopg2.connect(args.db_url)
    conn.autocommit = True

    # MIRACL
    miracl_queries = load_queries(MIRACL_QUERIES_PATH, limit=N_MIRACL)
    miracl_embs    = load_query_embeddings(conn, "miracl")
    print(f"  MIRACL: {len(miracl_queries)} queries, {len(miracl_embs)} embeddings")

    # EZIS
    ezis_queries = load_queries(EZIS_QUERIES_PATH)
    ezis_embs    = load_query_embeddings(conn, "ezis")
    print(f"  EZIS:   {len(ezis_queries)} queries, {len(ezis_embs)} embeddings")

    all_results = []
    all_results += bench_dataset(
        conn, "MIRACL", miracl_queries, miracl_embs,
        table="p7_hybrid_miracl", bm25_idx="idx_p7_miracl_bm25"
    )
    all_results += bench_dataset(
        conn, "EZIS", ezis_queries, ezis_embs,
        table="p7_hybrid_ezis", bm25_idx="idx_p7_ezis_bm25"
    )

    conn.close()

    os.makedirs(args.output_dir, exist_ok=True)
    json_path = os.path.join(args.output_dir, "phase7_hybrid.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "generated": datetime.now().isoformat(),
            "rrf_k": RRF_K,
            "topk": TOPK,
            "bayesian_alpha": BAYESIAN_ALPHA,
            "results": all_results,
        }, f, indent=2, ensure_ascii=False)
    print(f"  JSON: {json_path}")

    generate_report(all_results, args.output_dir)
    print("\nDone.")


if __name__ == "__main__":
    main()
