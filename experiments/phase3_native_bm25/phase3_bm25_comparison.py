"""Phase 3: PostgreSQL native BM25 comparison (pgvector-sparse + tokenizer variants).

Compares BM25 search quality and latency across tokenizers using pre-built
pgvector sparse tables in PostgreSQL.

MIRACL-ko (10k corpus, 213 queries) and EZIS (97 docs, 131 queries).

Usage:
    uv run python3 experiments/phase3_native_bm25/phase3_bm25_comparison.py \
        --db-url postgresql://postgres:postgres@localhost:5432/dev \
        --miracl-queries data/miracl/queries_dev.json \
        --ezis-chunks data/ezis/chunks.json \
        --ezis-queries data/ezis/queries.json \
        --output-dir results/phase3
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

import psycopg2

import psycopg2.extras
from experiments.common.bm25_module import BM25Embedder, BM25Embedder_PG, bm25_sparse_search


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def rebuild_sparse_table(
    conn, embedder: BM25Embedder_PG, corpus_texts: List[str],
    corpus_ids: List[str], table: str,
) -> None:
    """Drop + recreate sparse BM25 table with fresh embedder vocab_size."""
    vocab_size = embedder.vocab_size
    with conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {table}")
        cur.execute(f"""
            CREATE TABLE {table} (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                emb_sparse sparsevec({vocab_size})
            )
        """)
    conn.commit()
    print(f"    Rebuilding {table} (vocab_size={vocab_size}, {len(corpus_texts)} docs)...",
          end="", flush=True)
    t0 = time.perf_counter()
    batch = []
    for doc_id, text in zip(corpus_ids, corpus_texts):
        emb = embedder.embed_document(text)
        batch.append((doc_id, text, emb))
        if len(batch) >= 500:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(
                    cur, f"INSERT INTO {table} (id, text, emb_sparse) VALUES %s", batch
                )
            conn.commit()
            batch.clear()
    if batch:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur, f"INSERT INTO {table} (id, text, emb_sparse) VALUES %s", batch
            )
        conn.commit()
    print(f" done ({time.perf_counter()-t0:.1f}s)")


def get_table_dim(conn, table: str) -> int | None:
    """Return the sparsevec dimension of the table, or None if table missing."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name=%s AND table_schema='public'", (table,)
        )
        if not cur.fetchone():
            return None
        # Read one row to check actual dim
        cur.execute(f"SELECT emb_sparse FROM {table} LIMIT 1")
        row = cur.fetchone()
        if not row or row[0] is None:
            return None
        # sparsevec string format: "{indices}/{values}/{dim}"
        sv_str = str(row[0])
        parts = sv_str.rsplit("/", 1)
        if len(parts) == 2:
            try:
                return int(parts[1])
            except ValueError:
                pass
        return None


def search_sparse(conn, embedder: BM25Embedder_PG, query_text: str, table: str, k: int) -> List[str]:
    """Search sparse BM25 table using pgvector inner product."""
    q_vec = embedder.embed_query(query_text)
    if q_vec is None:
        return []
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT id FROM {table} ORDER BY emb_sparse <#> %s::sparsevec LIMIT %s",
            (q_vec, k)
        )
        return [row[0] for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def ndcg_at_k(ranked_ids: List[str], relevant_ids: set, k: int = 10) -> float:
    dcg = sum(1.0 / math.log2(r + 2)
              for r, d in enumerate(ranked_ids[:k]) if d in relevant_ids)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(min(len(relevant_ids), k)))
    return dcg / idcg if idcg > 0 else 0.0


def recall_at_k(ranked_ids: List[str], relevant_ids: set, k: int = 10) -> float:
    hits = sum(1 for d in ranked_ids[:k] if d in relevant_ids)
    return hits / len(relevant_ids) if relevant_ids else 0.0


def mrr_score(ranked_ids: List[str], relevant_ids: set) -> float:
    for rank, doc_id in enumerate(ranked_ids, start=1):
        if doc_id in relevant_ids:
            return 1.0 / rank
    return 0.0


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

def evaluate_search(
    method_id: str,
    method_name: str,
    search_fn,
    queries: List[Dict],
    k: int = 10,
) -> Dict:
    ndcg_scores, recall_scores, mrr_scores, latencies = [], [], [], []
    zero_result_count = 0

    for q in queries:
        rel_ids = set(str(r) for r in q.get("relevant_ids", []))
        if not rel_ids:
            continue
        t0 = time.perf_counter()
        ranked = search_fn(q["text"])
        latencies.append((time.perf_counter() - t0) * 1000)
        if not ranked:
            zero_result_count += 1
        ndcg_scores.append(ndcg_at_k(ranked, rel_ids, k))
        recall_scores.append(recall_at_k(ranked, rel_ids, k))
        mrr_scores.append(mrr_score(ranked, rel_ids))

    def mean(xs): return round(sum(xs) / len(xs), 4) if xs else 0.0
    def pct(xs, p):
        if not xs: return 0.0
        s = sorted(xs)
        return round(s[int(len(s) * p / 100)], 2)

    result = {
        "method_id": method_id,
        "method": method_name,
        "n_queries": len(ndcg_scores),
        "ndcg_at_10": mean(ndcg_scores),
        "recall_at_10": mean(recall_scores),
        "mrr": mean(mrr_scores),
        "zero_result_rate": round(zero_result_count / len(ndcg_scores), 3) if ndcg_scores else 1.0,
        "latency_p50_ms": pct(latencies, 50),
        "latency_p95_ms": pct(latencies, 95),
    }
    print(f"    NDCG@10={result['ndcg_at_10']:.4f}  R@10={result['recall_at_10']:.4f}"
          f"  MRR={result['mrr']:.4f}  zero_rate={result['zero_result_rate']:.2f}"
          f"  p50={result['latency_p50_ms']:.1f}ms")
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 3: pgvector-sparse BM25 comparison")
    parser.add_argument("--db-url", default="postgresql://postgres:postgres@localhost:5432/dev")
    parser.add_argument("--miracl-queries", default="data/miracl/queries_dev.json")
    parser.add_argument("--ezis-chunks",    default="data/ezis/chunks.json")
    parser.add_argument("--ezis-queries",   default="data/ezis/queries.json")
    parser.add_argument("--output-dir",     default="results/phase3")
    parser.add_argument("--k", type=int,    default=10)
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    print("[Phase 3] Loading queries...")
    miracl_queries = [{
        "text": q["text"],
        "relevant_ids": [str(r) for r in q.get("relevant_ids", [])],
    } for q in json.load(open(args.miracl_queries))]
    ezis_docs    = [{**c, "id": str(c["id"])} for c in json.load(open(args.ezis_chunks))]
    ezis_queries = [{
        "text": q["text"],
        "relevant_ids": [str(r) for r in q.get("relevant_ids", [])],
    } for q in json.load(open(args.ezis_queries))]

    print(f"  MIRACL: {len(miracl_queries)} queries (10k corpus in DB)")
    print(f"  EZIS:   {len(ezis_docs)} docs, {len(ezis_queries)} queries")

    conn = psycopg2.connect(args.db_url)
    from pgvector.psycopg2 import register_vector
    register_vector(conn)
    all_results: Dict[str, List] = {"miracl": [], "ezis": []}

    # -----------------------------------------------------------------------
    # MIRACL — use pre-built pgvector-sparse tables in DB
    # -----------------------------------------------------------------------
    print("\n[MIRACL] Loading corpus from text_embedding...")
    with conn.cursor() as cur:
        cur.execute("SELECT id::text, text FROM text_embedding ORDER BY id")
        rows = cur.fetchall()
    corpus_ids   = [r[0] for r in rows]
    corpus_texts = [r[1] for r in rows]
    print(f"  Loaded {len(corpus_texts)} docs")

    miracl_methods = [
        ("3-mecab",     "Mecab",     "text_embedding_sparse_bm25"),
        ("3-kiwi",      "kiwi-cong", "text_embedding_sparse_bm25_kiwi_cong"),
        ("3-okt",       "Okt",       "text_embedding_sparse_bm25_okt"),
    ]

    for method_id, tok_str, table_name in miracl_methods:
        print(f"\n[MIRACL] Method {method_id}: pgvector-sparse BM25 ({tok_str})...")

        # Fit BM25Embedder_PG on the corpus
        print(f"    Fitting BM25 ({tok_str}) on {len(corpus_texts)} docs...", end="", flush=True)
        t0 = time.perf_counter()
        embedder = BM25Embedder_PG(tokenizer=tok_str)
        embedder.fit(corpus_texts)
        print(f" done ({time.perf_counter()-t0:.1f}s), vocab={embedder.vocab_size}")

        # Check if table dimension matches; rebuild if stale
        table_dim = get_table_dim(conn, table_name)
        if table_dim != embedder.vocab_size:
            print(f"    Table dim mismatch ({table_dim} vs {embedder.vocab_size}) — rebuilding...")
            rebuild_sparse_table(conn, embedder, corpus_texts, corpus_ids, table_name)

        # Persist vocab so Phase 4 can restore the exact same token→index mapping
        vocab_cache = f".cache/bm25_vocab_{tok_str.replace('-','_')}.json"
        embedder.save_vocab(vocab_cache)
        print(f"    Saved vocab cache → {vocab_cache}")

        def make_search_fn(emb, tbl, k):
            def search_fn(query_text: str) -> List[str]:
                return search_sparse(conn, emb, query_text, tbl, k)
            return search_fn

        r = evaluate_search(
            method_id, f"pgvector-sparse BM25 ({tok_str})",
            make_search_fn(embedder, table_name, args.k),
            miracl_queries, args.k,
        )
        r["dataset"] = "miracl"
        r["tokenizer"] = tok_str
        all_results["miracl"].append(r)

    # -----------------------------------------------------------------------
    # EZIS — Python-side BM25 (97 docs, no DB table needed)
    # -----------------------------------------------------------------------
    print("\n[EZIS] Python-side BM25 comparison (in-memory, 97 docs)...")
    ezis_corpus_texts = [d["text"] for d in ezis_docs]
    ezis_corpus_ids   = [d["id"]   for d in ezis_docs]

    ezis_tokenizers = [
        ("3-ezis-mecab",  "Mecab",     "Mecab BM25"),
        ("3-ezis-kiwi",   "kiwi-cong", "kiwi-cong BM25"),
        ("3-ezis-okt",    "Okt",       "Okt BM25"),
        ("3-ezis-ws",     "whitespace","whitespace BM25"),
    ]

    for method_id, tok_str, method_name in ezis_tokenizers:
        print(f"\n[EZIS] Method {method_id}: {method_name}...")
        try:
            t0 = time.perf_counter()
            emb = BM25Embedder(tokenizer=tok_str)
            emb.fit(ezis_corpus_texts)
            doc_vecs = [emb.embed_document(t) for t in ezis_corpus_texts]
            print(f"    Fit done ({time.perf_counter()-t0:.1f}s)")

            def make_ezis_search(emb, doc_vecs, ids, k):
                def search_fn(query_text: str) -> List[str]:
                    q_vec = emb.embed_query(query_text)
                    if not q_vec:
                        return []
                    scored = sorted(
                        [(sum(q_vec.get(tok, 0.0) * w for tok, w in dv.items()), did)
                         for did, dv in zip(ids, doc_vecs)],
                        key=lambda x: -x[0]
                    )
                    return [did for _, did in scored[:k]]
                return search_fn

            r = evaluate_search(
                method_id, method_name,
                make_ezis_search(emb, doc_vecs, ezis_corpus_ids, args.k),
                ezis_queries, args.k,
            )
            r["dataset"] = "ezis"
            r["tokenizer"] = tok_str
            all_results["ezis"].append(r)
        except Exception as e:
            print(f"    FAILED: {e}")

    conn.close()

    # Save results
    json_path = os.path.join(args.output_dir, "phase3_bm25_comparison.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\n[Phase 3] Saved: {json_path}")

    # Summary
    print("\n" + "=" * 60)
    for ds in ["miracl", "ezis"]:
        print(f"\n{ds.upper()} results:")
        for r in sorted([x for x in all_results[ds] if x.get("ndcg_at_10") is not None],
                        key=lambda x: -x.get("ndcg_at_10", 0)):
            print(f"  {r['method_id']} {r['method']:35} NDCG@10={r['ndcg_at_10']:.4f}")


if __name__ == "__main__":
    main()
