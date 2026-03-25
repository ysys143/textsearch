"""
Phase 7 Section 2: Hybrid Search Benchmark (BGE-M3 1024-dim)

Methods measured on p7_hybrid_miracl (10K docs):
  - BM25 AND   : pg_textsearch <@> operator
  - Dense      : HNSW cosine (pgvector)
  - RRF        : BM25_AND(top-60) + Dense(top-60), k=60
  - BM25_OR    : GIN ts_rank_cd

NDCG@10, Recall@10, MRR on MIRACL-ko 213 queries.
Latency p50/p95/p99 (query emb pre-loaded from p7_query_emb).

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
N_QUERIES = 213
RRF_K = 60
RRF_TOPK = 60   # fetch top-N from each component before fusion
WARMUP = 5


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


def mrr(relevant_ids: List[str], retrieved_ids: List[str], k: int = 10) -> float:
    relevant_set = set(relevant_ids)
    for rank, doc_id in enumerate(retrieved_ids[:k], start=1):
        if doc_id in relevant_set:
            return 1.0 / rank
    return 0.0


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_miracl_queries() -> List[dict]:
    with open(MIRACL_QUERIES_PATH, encoding="utf-8") as f:
        return json.load(f)[:N_QUERIES]


def load_query_embeddings(conn) -> Dict[str, List[float]]:
    """Load pre-computed query embeddings from p7_query_emb."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT query_id, emb FROM p7_query_emb WHERE dataset = 'miracl'"
        )
        rows = cur.fetchall()
    return {r[0]: r[1] for r in rows}


# ---------------------------------------------------------------------------
# Search functions
# ---------------------------------------------------------------------------

def search_bm25_and(conn, query_text: str, k: int = 10) -> List[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM p7_hybrid_miracl ORDER BY text <@> %s LIMIT %s",
            (query_text, k)
        )
        return [r[0] for r in cur.fetchall()]


def search_dense(conn, query_emb: List[float], k: int = 10) -> List[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM p7_hybrid_miracl ORDER BY dense_vec <=> %s LIMIT %s",
            (query_emb, k)
        )
        return [r[0] for r in cur.fetchall()]


def search_bm25_or(conn, query_text: str, k: int = 10) -> List[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT tsvector_to_array(to_tsvector('public.korean', %s))",
            (query_text,)
        )
        tokens = cur.fetchone()[0]
        if not tokens:
            return []
        or_query = " | ".join(tokens)
        cur.execute("""
            SELECT id, ts_rank_cd(tsv, to_tsquery('public.korean', %s)) AS score
            FROM p7_hybrid_miracl
            WHERE tsv @@ to_tsquery('public.korean', %s)
            ORDER BY score DESC
            LIMIT %s
        """, (or_query, or_query, k))
        return [r[0] for r in cur.fetchall()]


def search_rrf(conn, query_text: str, query_emb: List[float], k: int = 10) -> List[str]:
    """RRF fusion: BM25 AND + Dense, each fetches top RRF_TOPK candidates."""
    bm25_ids = search_bm25_and(conn, query_text, k=RRF_TOPK)
    dense_ids = search_dense(conn, query_emb, k=RRF_TOPK)

    scores: Dict[str, float] = {}
    for rank, doc_id in enumerate(bm25_ids, start=1):
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (RRF_K + rank)
    for rank, doc_id in enumerate(dense_ids, start=1):
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (RRF_K + rank)

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [doc_id for doc_id, _ in ranked[:k]]


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

def run_method(
    name: str,
    fn,
    queries: List[dict],
    query_embs: Dict[str, List[float]],
) -> dict:
    """Measure latency + quality for one search method."""
    ndcg_scores, recall_scores, mrr_scores, latencies = [], [], [], []

    # Warmup
    for q in queries[:WARMUP]:
        emb = query_embs.get(q["query_id"])
        fn(q["text"], emb)

    for q in queries:
        emb = query_embs.get(q["query_id"])
        t0 = time.perf_counter()
        retrieved = fn(q["text"], emb)
        latencies.append((time.perf_counter() - t0) * 1000)

        rel = q.get("relevant_ids", [])
        if rel:
            ndcg_scores.append(ndcg_at_k(rel, retrieved))
            recall_scores.append(recall_at_k(rel, retrieved))
            mrr_scores.append(mrr(rel, retrieved))

    latencies.sort()
    n = len(latencies)
    nq = len(ndcg_scores)

    result = {
        "method": name,
        "n_queries": len(queries),
        "ndcg_at_10": round(sum(ndcg_scores) / nq, 4) if nq else None,
        "recall_at_10": round(sum(recall_scores) / nq, 4) if nq else None,
        "mrr": round(sum(mrr_scores) / nq, 4) if nq else None,
        "latency_p50": round(latencies[n // 2], 2),
        "latency_p95": round(latencies[int(n * 0.95)], 2),
        "latency_p99": round(latencies[int(n * 0.99)], 2),
    }

    print(f"  [{name:12s}] NDCG@10={result['ndcg_at_10']}  "
          f"p50={result['latency_p50']}ms  p95={result['latency_p95']}ms")
    return result


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def generate_report(results: List[dict], output_dir: str) -> str:
    lines = [
        "# Phase 7 Section 2: Hybrid Search Benchmark",
        "",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "**Corpus:** p7_hybrid_miracl (10K docs, BGE-M3 1024-dim)",
        "**Queries:** MIRACL-ko dev 213",
        "",
        "---",
        "",
        "## Quality (NDCG@10 / Recall@10 / MRR)",
        "",
        "| Method | NDCG@10 | Recall@10 | MRR |",
        "|--------|---------|-----------|-----|",
    ]
    for r in results:
        lines.append(
            f"| {r['method']} "
            f"| {r['ndcg_at_10']} "
            f"| {r['recall_at_10']} "
            f"| {r['mrr']} |"
        )

    lines += [
        "",
        "## Latency (ms)",
        "",
        "| Method | p50 | p95 | p99 |",
        "|--------|-----|-----|-----|",
    ]
    for r in results:
        lines.append(
            f"| {r['method']} "
            f"| {r['latency_p50']}ms "
            f"| {r['latency_p95']}ms "
            f"| {r['latency_p99']}ms |"
        )

    lines += [
        "",
        "---",
        "",
        "## 비고",
        "",
        "- **BM25 AND**: pg_textsearch `<@>` 연산자 (AND matching)",
        "- **Dense**: HNSW cosine (pgvector), 쿼리 임베딩 pre-computed",
        f"- **RRF**: BM25_AND(top-{RRF_TOPK}) + Dense(top-{RRF_TOPK}), k={RRF_K}",
        "- **BM25 OR**: GIN + ts_rank_cd (OR matching)",
        "",
    ]

    os.makedirs(output_dir, exist_ok=True)
    report_path = os.path.join(output_dir, "phase7_hybrid_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n  Report: {report_path}")
    return report_path


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

    queries = load_miracl_queries()
    print(f"  Queries: {len(queries)}")

    print("  Loading query embeddings from p7_query_emb...")
    query_embs = load_query_embeddings(conn)
    print(f"  Loaded {len(query_embs)} query embeddings")

    # Build search functions (emb arg passed but may be None for BM25-only)
    methods = [
        ("BM25 AND",  lambda t, e: search_bm25_and(conn, t)),
        ("Dense",     lambda t, e: search_dense(conn, e) if e else []),
        ("RRF",       lambda t, e: search_rrf(conn, t, e) if e else []),
        ("BM25 OR",   lambda t, e: search_bm25_or(conn, t)),
    ]

    print("\n--- Benchmarking ---")
    results = []
    for name, fn in methods:
        result = run_method(name, fn, queries, query_embs)
        results.append(result)

    conn.close()

    # Save JSON
    os.makedirs(args.output_dir, exist_ok=True)
    json_path = os.path.join(args.output_dir, "phase7_hybrid.json")
    output = {
        "generated": datetime.now().isoformat(),
        "corpus": "p7_hybrid_miracl",
        "n_queries": len(queries),
        "results": results,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"  JSON: {json_path}")

    generate_report(results, args.output_dir)
    print("\nDone.")


if __name__ == "__main__":
    main()
