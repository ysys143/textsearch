"""
Phase 6-3: VectorChord-BM25 vs pl/pgsql BM25 v2 — Scaling Test

측정 항목:
  - 1K / 10K / 100K docs 규모별 insert + index build time
  - query latency p50/p95 (213 MIRACL queries)
  - index size on disk
  - EXPLAIN ANALYZE (10K 기준)

Usage:
  uv run python3 experiments/phase6_vectorchord/phase6_3_scaling.py \\
    --db-url postgresql://postgres:postgres@localhost:5436/dev \\
    --main-db-url postgresql://postgres:postgres@localhost:5432/dev \\
    --output-dir results/phase6
"""

import argparse
import json
import math
import os
import time
from collections import Counter
from datetime import datetime
from typing import Dict, List

import psycopg2
import psycopg2.extras

QUERIES_PATH = "data/miracl/queries_dev.json"
SCALES = [1_000, 10_000, 100_000]
N_QUERY_SAMPLES = 213  # MIRACL dev queries


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def connect(db_url: str, label: str):
    try:
        conn = psycopg2.connect(db_url)
        conn.autocommit = False
        print(f"  [OK] {label}")
        return conn
    except Exception as e:
        print(f"  [FAIL] {label}: {e}")
        return None


def load_queries() -> List[dict]:
    with open(QUERIES_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_corpus_from_db(conn_main) -> List[dict]:
    with conn_main.cursor() as cur:
        cur.execute("SELECT id, text FROM text_embedding ORDER BY id")
        rows = cur.fetchall()
    return [{"id": str(r[0]), "text": r[1]} for r in rows]


def build_vocab(conn_main, texts: List[str], batch_size: int = 500) -> dict:
    vocab = {}
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        with conn_main.cursor() as cur:
            for text in batch:
                cur.execute(
                    "SELECT (unnest(to_tsvector('public.korean', %s))).lexeme", (text,))
                for (lexeme,) in cur.fetchall():
                    if lexeme not in vocab:
                        vocab[lexeme] = len(vocab)
    return vocab


def text_to_bm25vector(conn_main, text: str, vocab: dict) -> dict:
    with conn_main.cursor() as cur:
        cur.execute(
            "SELECT lexeme, array_length(positions, 1)"
            " FROM unnest(to_tsvector('public.korean', %s))", (text,))
        rows = cur.fetchall()
    return {vocab[r[0]]: r[1] for r in rows if r[0] in vocab}


def measure_latency(fn, queries: List[dict], n: int = None) -> dict:
    qs = queries[:n] if n else queries
    latencies = []
    for q in qs:
        t0 = time.perf_counter()
        fn(q["text"])
        latencies.append((time.perf_counter() - t0) * 1000)
    latencies.sort()
    return {
        "p50": round(latencies[len(latencies) // 2], 2),
        "p95": round(latencies[int(len(latencies) * 0.95)], 2),
        "p99": round(latencies[int(len(latencies) * 0.99)], 2),
    }


# ---------------------------------------------------------------------------
# Scale corpus: replicate 10K base to reach target size
# ---------------------------------------------------------------------------

def scale_corpus(base_docs: List[dict], target_size: int) -> List[dict]:
    """Replicate base docs to reach target_size with unique IDs."""
    result = []
    i = 0
    while len(result) < target_size:
        doc = base_docs[i % len(base_docs)]
        suffix = i // len(base_docs)
        new_id = f"{doc['id']}_{suffix}" if suffix > 0 else str(doc['id'])
        result.append({"id": new_id, "text": doc["text"]})
        i += 1
    return result[:target_size]


# ---------------------------------------------------------------------------
# VectorChord-BM25 scaling
# ---------------------------------------------------------------------------

def run_vchord_scale(conn_p6, conn_main, docs: List[dict], vocab: dict,
                     queries: List[dict], scale: int) -> dict:
    table = f"t6_scale_{scale // 1000}k"
    idx = f"{table}_emb_idx"

    # Setup
    with conn_p6.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {table}")
        cur.execute(f"""
            CREATE TABLE {table} (
                id TEXT PRIMARY KEY,
                emb bm25vector
            )
        """)
    conn_p6.commit()

    # Insert
    t0 = time.perf_counter()
    batch = []
    skipped = 0
    for doc in docs:
        vec = text_to_bm25vector(conn_main, doc["text"], vocab)
        if not vec:
            skipped += 1
            continue
        entries = ",".join(f"{k}:{v}" for k, v in sorted(vec.items()))
        batch.append((doc["id"], f"{{{entries}}}"))
        if len(batch) >= 200:
            with conn_p6.cursor() as cur:
                psycopg2.extras.execute_batch(
                    cur,
                    f"INSERT INTO {table} (id, emb) VALUES (%s, %s::bm25vector)",
                    batch, page_size=200)
            conn_p6.commit()
            batch = []
    if batch:
        with conn_p6.cursor() as cur:
            psycopg2.extras.execute_batch(
                cur,
                f"INSERT INTO {table} (id, emb) VALUES (%s, %s::bm25vector)",
                batch, page_size=200)
        conn_p6.commit()
    insert_sec = round(time.perf_counter() - t0, 2)
    print(f"    insert: {scale - skipped} docs in {insert_sec}s ({(scale - skipped) / insert_sec:.0f} docs/sec)")

    # Index build
    t0 = time.perf_counter()
    with conn_p6.cursor() as cur:
        cur.execute("SET maintenance_work_mem = '32MB'")
        cur.execute("SET max_parallel_maintenance_workers = 0")
        cur.execute(f"CREATE INDEX {idx} ON {table} USING bm25 (emb bm25_ops)")
    conn_p6.commit()
    index_sec = round(time.perf_counter() - t0, 2)
    print(f"    index build: {index_sec}s")

    # Index size
    with conn_p6.cursor() as cur:
        cur.execute(f"SELECT pg_size_pretty(pg_relation_size('{idx}'))")
        idx_size = cur.fetchone()[0]
        cur.execute(f"SELECT pg_size_pretty(pg_total_relation_size('{table}'))")
        total_size = cur.fetchone()[0]
    print(f"    index size: {idx_size} (total table: {total_size})")

    # Query latency
    def search(query_text: str):
        vec = text_to_bm25vector(conn_main, query_text, vocab)
        if not vec:
            return []
        entries = ",".join(f"{k}:{v}" for k, v in sorted(vec.items()))
        q_vec = f"{{{entries}}}"
        with conn_p6.cursor() as cur:
            cur.execute(
                f"SELECT id FROM {table}"
                f" ORDER BY emb <&> to_bm25query('{idx}', %s::bm25vector) LIMIT 10",
                (q_vec,))
            return [r[0] for r in cur.fetchall()]

    lat = measure_latency(search, queries)
    print(f"    latency: p50={lat['p50']}ms p95={lat['p95']}ms")

    # EXPLAIN ANALYZE at 10K
    explain_out = None
    if scale == 10_000:
        q = queries[0]
        vec = text_to_bm25vector(conn_main, q["text"], vocab)
        if vec:
            entries = ",".join(f"{k}:{v}" for k, v in sorted(vec.items()))
            q_vec = f"{{{entries}}}"
            with conn_p6.cursor() as cur:
                cur.execute(
                    f"EXPLAIN ANALYZE SELECT id FROM {table}"
                    f" ORDER BY emb <&> to_bm25query('{idx}', %s::bm25vector) LIMIT 10",
                    (q_vec,))
                explain_out = "\n".join(r[0] for r in cur.fetchall())

    return {
        "scale": scale,
        "insert_sec": insert_sec,
        "index_build_sec": index_sec,
        "index_size": idx_size,
        "total_size": total_size,
        **{f"latency_{k}": v for k, v in lat.items()},
        "explain": explain_out,
    }


# ---------------------------------------------------------------------------
# pl/pgsql BM25 v2 scaling
# ---------------------------------------------------------------------------

def run_plpgsql_scale(conn_main, docs: List[dict], queries: List[dict],
                      scale: int) -> dict:
    table = f"t5_scale_{scale // 1000}k"
    inv = f"inv_scale_{scale // 1000}k"

    with conn_main.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        cur.execute(f"""
            CREATE TABLE {table} (
                id TEXT PRIMARY KEY,
                text TEXT,
                tsv tsvector
            )
        """)
    conn_main.commit()

    # Insert
    t0 = time.perf_counter()
    psycopg2.extras.execute_batch(
        conn_main.cursor(),
        f"INSERT INTO {table} (id, text) VALUES (%s, %s)",
        [(d["id"], d["text"]) for d in docs], page_size=500)
    conn_main.commit()
    with conn_main.cursor() as cur:
        cur.execute(f"UPDATE {table} SET tsv = to_tsvector('public.korean', text)")
    conn_main.commit()
    insert_sec = round(time.perf_counter() - t0, 2)
    print(f"    insert: {scale} docs in {insert_sec}s")

    # Inverted index build
    t0 = time.perf_counter()
    with conn_main.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {inv}")
        cur.execute(f"""
            CREATE TABLE {inv} (
                term TEXT, doc_id TEXT, term_freq INT, doc_length INT,
                PRIMARY KEY (term, doc_id)
            )
        """)
        cur.execute(f"""
            INSERT INTO {inv} (term, doc_id, term_freq, doc_length)
            SELECT term, id, cnt,
                   SUM(cnt) OVER (PARTITION BY id) AS doc_length
            FROM (
                SELECT id, (ts_row).lexeme AS term,
                       array_length((ts_row).positions, 1) AS cnt
                FROM (
                    SELECT id, unnest(to_tsvector('public.korean', text)) AS ts_row
                    FROM {table}
                ) sub
            ) agg
        """)
        cur.execute(f"CREATE INDEX ON {inv}(term)")
        cur.execute(f"CREATE INDEX ON {inv}(doc_id)")
    conn_main.commit()
    index_sec = round(time.perf_counter() - t0, 2)
    print(f"    inverted index build: {index_sec}s")

    # bm25_stats table
    with conn_main.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS bm25_stats_scale CASCADE")
        cur.execute("""
            CREATE TABLE bm25_stats_scale (
                id INT PRIMARY KEY DEFAULT 1,
                total_docs INT, avg_doc_length FLOAT
            )
        """)
        cur.execute(f"""
            INSERT INTO bm25_stats_scale (total_docs, avg_doc_length)
            SELECT COUNT(DISTINCT doc_id), AVG(doc_length)
            FROM (
                SELECT doc_id, MAX(doc_length) AS doc_length FROM {inv} GROUP BY doc_id
            ) per_doc
        """)
        cur.execute("DROP TABLE IF EXISTS bm25_df_scale")
        cur.execute(f"""
            CREATE TABLE bm25_df_scale AS
            SELECT term, COUNT(DISTINCT doc_id)::INT AS df FROM {inv} GROUP BY term
        """)
        cur.execute("ALTER TABLE bm25_df_scale ADD PRIMARY KEY (term)")
        # Create ranking function
        cur.execute(f"""
            CREATE OR REPLACE FUNCTION bm25_scale_ranking(
                query TEXT, k1 FLOAT DEFAULT 1.2, b FLOAT DEFAULT 0.75
            )
            RETURNS TABLE(doc_id TEXT, score FLOAT) AS $$
            DECLARE
                avgdl FLOAT; n_docs INT;
            BEGIN
                SELECT avg_doc_length, total_docs INTO avgdl, n_docs FROM bm25_stats_scale;
                RETURN QUERY
                SELECT i.doc_id,
                    SUM(LOG((n_docs - df.df + 0.5)/(df.df + 0.5) + 1) *
                        (i.term_freq * (k1 + 1)) /
                        (i.term_freq + k1 * (1 - b + b * (i.doc_length::float / avgdl)))
                    )::FLOAT
                FROM {inv} i
                JOIN bm25_df_scale df ON i.term = df.term
                WHERE i.term = ANY(tsvector_to_array(to_tsvector('public.korean', query)))
                GROUP BY i.doc_id ORDER BY score DESC;
            END;
            $$ LANGUAGE plpgsql;
        """)
    conn_main.commit()

    # Index size
    with conn_main.cursor() as cur:
        cur.execute(f"SELECT pg_size_pretty(pg_total_relation_size('{inv}'))")
        total_size = cur.fetchone()[0]
    print(f"    inverted index size: {total_size}")

    # Query latency
    def search(query_text: str):
        with conn_main.cursor() as cur:
            cur.execute("SELECT doc_id FROM bm25_scale_ranking(%s) LIMIT 10", (query_text,))
            return [r[0] for r in cur.fetchall()]

    lat = measure_latency(search, queries)
    print(f"    latency: p50={lat['p50']}ms p95={lat['p95']}ms")

    # EXPLAIN ANALYZE at 10K
    explain_out = None
    if scale == 10_000:
        with conn_main.cursor() as cur:
            cur.execute(
                "EXPLAIN ANALYZE SELECT doc_id FROM bm25_scale_ranking(%s) LIMIT 10",
                (queries[0]["text"],))
            explain_out = "\n".join(r[0] for r in cur.fetchall())

    return {
        "scale": scale,
        "insert_sec": insert_sec,
        "index_build_sec": index_sec,
        "inv_index_size": total_size,
        **{f"latency_{k}": v for k, v in lat.items()},
        "explain": explain_out,
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def write_report(output_dir: str, results: dict) -> str:
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "phase6_3_scaling_report.md")

    lines = [
        "# Phase 6-3: VectorChord-BM25 vs pl/pgsql BM25 v2 — Scaling Test",
        "",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "---",
        "",
        "## Latency vs Scale",
        "",
        "### VectorChord-BM25 (bm25vector + Block-WeakAnd)",
        "",
        "| Scale | Insert | Index Build | Index Size | p50 | p95 |",
        "|-------|--------|-------------|------------|-----|-----|",
    ]
    for r in results.get("vchord", []):
        lines.append(
            f"| {r['scale']:,} | {r['insert_sec']}s | {r['index_build_sec']}s"
            f" | {r['index_size']} | {r['latency_p50']}ms | {r['latency_p95']}ms |"
        )

    lines += [
        "",
        "### pl/pgsql BM25 v2 (inverted_index B-tree + stats tables)",
        "",
        "| Scale | Insert | Index Build | Index Size | p50 | p95 |",
        "|-------|--------|-------------|------------|-----|-----|",
    ]
    for r in results.get("plpgsql", []):
        lines.append(
            f"| {r['scale']:,} | {r['insert_sec']}s | {r['index_build_sec']}s"
            f" | {r['inv_index_size']} | {r['latency_p50']}ms | {r['latency_p95']}ms |"
        )

    lines += ["", "---", "", "## EXPLAIN ANALYZE (10K docs)", ""]
    for system, key in [("VectorChord-BM25", "vchord"), ("pl/pgsql BM25 v2", "plpgsql")]:
        explain = next(
            (r.get("explain") for r in results.get(key, []) if r["scale"] == 10_000), None)
        if explain:
            lines += [f"### {system}", "", "```", explain, "```", ""]

    content = "\n".join(lines) + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-url", default="postgresql://postgres:postgres@localhost:5436/dev")
    parser.add_argument("--main-db-url", default="postgresql://postgres:postgres@localhost:5432/dev")
    parser.add_argument("--output-dir", default="results/phase6")
    parser.add_argument("--scales", default="1000,10000,100000",
                        help="Comma-separated scale sizes")
    parser.add_argument("--skip-plpgsql", action="store_true",
                        help="Skip pl/pgsql scaling (only run VectorChord)")
    args = parser.parse_args()

    scales = [int(s) for s in args.scales.split(",")]

    print("=" * 60)
    print("Phase 6-3: Scaling Test")
    print("=" * 60)

    conn_p6 = connect(args.db_url, "phase6 DB (VectorChord-BM25)")
    conn_main = connect(args.main_db_url, "main DB (textsearch_ko)")
    if not conn_p6 or not conn_main:
        return

    print("\n[1] Loading base corpus (10K)...")
    base_docs = load_corpus_from_db(conn_main)
    queries = load_queries()
    print(f"  {len(base_docs)} docs | {len(queries)} queries")

    print("\n[2] Building vocab...")
    vocab = build_vocab(conn_main, [d["text"] for d in base_docs])
    print(f"  Vocab: {len(vocab)} terms")

    results = {"vchord": [], "plpgsql": []}

    # Create extension once, then reconnect so type cache is fresh
    print("\n[3] VectorChord-BM25 scaling...")
    with conn_p6.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vchord_bm25")
    conn_p6.commit()
    conn_p6.close()
    conn_p6 = connect(args.db_url, "phase6 DB (reconnect after ext)")
    with conn_p6.cursor() as cur:
        cur.execute("SET search_path = public, bm25_catalog")
    conn_p6.commit()
    for scale in scales:
        print(f"\n  -- {scale:,} docs --")
        docs = scale_corpus(base_docs, scale)
        try:
            r = run_vchord_scale(conn_p6, conn_main, docs, vocab, queries, scale)
            results["vchord"].append(r)
        except Exception as e:
            print(f"    [SKIP] scale={scale:,}: {e}")
            # Reconnect for next scale attempt
            try:
                conn_p6.close()
            except Exception:
                pass
            import time as _t; _t.sleep(5)
            conn_p6 = connect(args.db_url, "phase6 DB (reconnect after crash)")
            if conn_p6:
                with conn_p6.cursor() as cur:
                    cur.execute("SET search_path = public, bm25_catalog")
                conn_p6.commit()

    if not args.skip_plpgsql:
        print("\n[4] pl/pgsql BM25 v2 scaling...")
        for scale in scales:
            print(f"\n  -- {scale:,} docs --")
            docs = scale_corpus(base_docs, scale)
            r = run_plpgsql_scale(conn_main, docs, queries, scale)
            results["plpgsql"].append(r)

    # Save
    report_path = write_report(args.output_dir, results)
    json_path = os.path.join(args.output_dir, "phase6_3_scaling.json")
    # Remove explain from JSON (too verbose)
    json_results = {
        k: [{kk: vv for kk, vv in r.items() if kk != "explain"} for r in v]
        for k, v in results.items()
    }
    with open(json_path, "w") as f:
        json.dump(json_results, f, indent=2)

    print(f"\n  Report: {report_path}")
    print(f"  JSON:   {json_path}")


if __name__ == "__main__":
    main()
