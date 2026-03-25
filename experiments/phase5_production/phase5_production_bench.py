"""Phase 5: Production PostgreSQL BM25/Hybrid 최적 세팅 벤치마크.

3가지 BM25 후보를 production 요구사항(R1~R5) 관점에서 평가:
  5-T:  pg_textsearch + MeCab (AND vs OR query)
  5-B:  pl/pgsql BM25 + MeCab (현재 vs stats 최적화)
  5-A:  pgvector-sparse BM25 (kiwi-cong) IDF staleness

Usage:
    source .venv/bin/activate
    python3 experiments/phase5_production/phase5_production_bench.py \
        --db-url postgresql://postgres:postgres@localhost:5432/dev \
        --output-dir results/phase5 \
        --experiments all
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import psycopg2
import psycopg2.extras
from pgvector.psycopg2 import register_vector, SparseVector


# ---------------------------------------------------------------------------
# Metrics (shared across all experiments)
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

def mean(xs): return round(sum(xs) / len(xs), 4) if xs else 0.0
def pct(xs, p):
    s = sorted(xs)
    return round(s[int(len(s) * p / 100)], 2) if s else 0.0


def evaluate(search_fn: Callable, queries: List[Dict], k: int = 10,
             n_warm: int = 3) -> Dict:
    """Run quality + latency evaluation."""
    # Warmup
    for q in queries[:n_warm]:
        search_fn(q["text"])

    ndcgs, recalls, mrrs, lats = [], [], [], []
    for q in queries:
        rel = set(str(r) for r in q.get("relevant_ids", []))
        if not rel:
            continue
        t0 = time.perf_counter()
        ranked = [str(r) for r in search_fn(q["text"])]
        lats.append((time.perf_counter() - t0) * 1000)
        ndcgs.append(ndcg_at_k(ranked, rel, k))
        recalls.append(recall_at_k(ranked, rel, k))
        mrrs.append(mrr_score(ranked, rel))

    return {
        "n_queries": len(ndcgs),
        "ndcg_at_10": mean(ndcgs),
        "recall_at_10": mean(recalls),
        "mrr": mean(mrrs),
        "latency_p50_ms": pct(lats, 50),
        "latency_p95_ms": pct(lats, 95),
        "latency_p99_ms": pct(lats, 99),
    }


def measure_qps(search_fn: Callable, queries: List[Dict],
                concurrency_levels: List[int] = [1, 4, 8, 16],
                n_queries: int = 100) -> Dict[int, float]:
    """Measure queries-per-second at various concurrency levels."""
    query_texts = [q["text"] for q in queries if q.get("relevant_ids")]
    # Cycle through queries if fewer than n_queries
    test_queries = []
    for i in range(n_queries):
        test_queries.append(query_texts[i % len(query_texts)])

    results = {}
    for c in concurrency_levels:
        # Warmup
        for q in test_queries[:3]:
            search_fn(q)

        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=c) as pool:
            futures = [pool.submit(search_fn, q) for q in test_queries]
            for f in as_completed(futures):
                f.result()  # raise if error
        elapsed = time.perf_counter() - t0
        qps = round(n_queries / elapsed, 1)
        results[c] = qps
        print(f"    QPS@{c} = {qps} ({elapsed:.2f}s for {n_queries} queries)")
    return results


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_data() -> Tuple[List[Dict], List[Dict], List[Dict], List[Dict]]:
    miracl_docs = json.loads(Path("data/miracl/docs_ko_miracl.json").read_text())
    miracl_queries = json.loads(Path("data/miracl/queries_dev.json").read_text())
    ezis_chunks = json.loads(Path("data/ezis/chunks.json").read_text())
    ezis_queries = json.loads(Path("data/ezis/queries.json").read_text())
    ezis_docs = [{"id": c["id"], "text": c["text"]} for c in ezis_chunks]
    print(f"[Data] MIRACL: {len(miracl_docs)} docs, {len(miracl_queries)} queries")
    print(f"[Data] EZIS:   {len(ezis_docs)} docs, {len(ezis_queries)} queries")
    return miracl_docs, miracl_queries, ezis_docs, ezis_queries


def load_miracl_from_db(db_url: str) -> List[Dict]:
    """Load all MIRACL docs from text_embedding table on main DB."""
    conn = psycopg2.connect(db_url)
    with conn.cursor() as cur:
        cur.execute("SELECT id, text FROM text_embedding ORDER BY id")
        rows = cur.fetchall()
    conn.close()
    docs = [{"id": str(r[0]), "text": r[1]} for r in rows]
    print(f"[Data] MIRACL (DB): {len(docs)} docs loaded from text_embedding")
    return docs


# ---------------------------------------------------------------------------
# DB setup helpers
# ---------------------------------------------------------------------------

def get_conn(db_url: str):
    conn = psycopg2.connect(db_url)
    conn.autocommit = False
    return conn


def setup_phase5_table(conn, table: str, docs: List[Dict],
                       config_name: str = "public.korean") -> None:
    """Create table with tsv column + GIN index + BM25 index."""
    with conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        cur.execute(f"""
            CREATE TABLE {table} (
                id TEXT PRIMARY KEY,
                text TEXT,
                tsv tsvector
            )
        """)
        psycopg2.extras.execute_batch(cur,
            f"INSERT INTO {table} (id, text) VALUES (%s, %s)",
            [(str(d["id"]), d["text"]) for d in docs],
            page_size=500)
        cur.execute(f"UPDATE {table} SET tsv = to_tsvector('{config_name}', text)")
        cur.execute(f"CREATE INDEX idx_{table}_tsv ON {table} USING GIN(tsv)")
    conn.commit()
    print(f"  [{table}] {len(docs)} docs loaded, GIN index created")


def setup_bm25_index(conn, table: str, config_name: str = "public.korean") -> bool:
    """Create pg_textsearch BM25 index."""
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS pg_textsearch")
            cur.execute(f"DROP INDEX IF EXISTS idx_{table}_bm25")
            cur.execute(f"""
                CREATE INDEX idx_{table}_bm25
                ON {table}
                USING bm25(text)
                WITH (text_config='{config_name}')
            """)
        conn.commit()
        print(f"  [{table}] BM25 index created")
        return True
    except Exception as e:
        conn.rollback()
        print(f"  [{table}] BM25 index FAILED: {e}")
        return False


# ============================================================================
# Experiment 5-T: pg_textsearch AND vs OR query
# ============================================================================

def search_bm25_and(conn, table: str, query_text: str, k: int = 10) -> List[str]:
    """pg_textsearch BM25 search with AND matching (default <@> operator)."""
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT id FROM {table}
            ORDER BY text <@> %s
            LIMIT %s
        """, (query_text, k))
        return [str(r[0]) for r in cur.fetchall()]


def search_bm25_or_tsrank(conn, table: str, query_text: str,
                          config: str = "public.korean", k: int = 10) -> List[str]:
    """OR-query using GIN + ts_rank_cd (fallback if <@> doesn't support OR)."""
    with conn.cursor() as cur:
        # Extract tokens from MeCab, build OR tsquery
        cur.execute(f"SELECT tsvector_to_array(to_tsvector('{config}', %s))", (query_text,))
        tokens = cur.fetchone()[0]
        if not tokens:
            return []
        or_query = " | ".join(tokens)
        cur.execute(f"""
            SELECT id, ts_rank_cd(tsv, to_tsquery('{config}', %s)) AS score
            FROM {table}
            WHERE tsv @@ to_tsquery('{config}', %s)
            ORDER BY score DESC
            LIMIT %s
        """, (or_query, or_query, k))
        return [str(r[0]) for r in cur.fetchall()]


def search_bm25_or_wand(conn, table: str, query_text: str,
                        config: str = "public.korean", k: int = 10) -> List[str]:
    """OR-query attempting BM25 <@> with explicit OR tsquery construction.

    pg_textsearch <@> operator uses plainto_tsquery internally (AND).
    We try passing OR tsquery string directly to see if <@> respects it.
    If this doesn't work, we fall back to ts_rank_cd.
    """
    with conn.cursor() as cur:
        # Extract tokens
        cur.execute(f"SELECT tsvector_to_array(to_tsvector('{config}', %s))", (query_text,))
        tokens = cur.fetchone()[0]
        if not tokens:
            return []
        or_text = " ".join(tokens)  # space-separated for <@> (may still AND)
        try:
            cur.execute(f"""
                SELECT id FROM {table}
                ORDER BY text <@> %s
                LIMIT %s
            """, (or_text, k))
            return [str(r[0]) for r in cur.fetchall()]
        except Exception:
            conn.rollback()
            return []


def run_experiment_5T(db_url: str, miracl_docs, miracl_queries,
                      ezis_docs, ezis_queries) -> Dict:
    """Experiment 5-T: pg_textsearch AND vs OR query comparison."""
    print("\n" + "=" * 70)
    print("Experiment 5-T: pg_textsearch AND vs OR query")
    print("=" * 70)

    results = {}

    for ds_label, docs, queries, table in [
        ("miracl", miracl_docs, miracl_queries, "phase5_t_miracl"),
        ("ezis", ezis_docs, ezis_queries, "phase5_t_ezis"),
    ]:
        print(f"\n--- {ds_label.upper()} ---")
        conn = get_conn(db_url)
        setup_phase5_table(conn, table, docs)
        has_bm25 = setup_bm25_index(conn, table)

        # 1. AND query (current behavior via <@>)
        if has_bm25:
            print(f"  [AND] Evaluating...")
            r_and = evaluate(
                lambda q, c=conn, t=table: search_bm25_and(c, t, q),
                queries
            )
            results[f"5T_{ds_label}_and"] = {**r_and, "method": "pg_textsearch AND (<@>)"}
            print(f"    → NDCG={r_and['ndcg_at_10']:.4f} R@10={r_and['recall_at_10']:.4f} "
                  f"p50={r_and['latency_p50_ms']}ms")

        # 2. OR query via ts_rank_cd + GIN
        print(f"  [OR ts_rank_cd] Evaluating...")
        r_or = evaluate(
            lambda q, c=conn, t=table: search_bm25_or_tsrank(c, t, q),
            queries
        )
        results[f"5T_{ds_label}_or_tsrank"] = {**r_or, "method": "OR tsquery + ts_rank_cd"}
        print(f"    → NDCG={r_or['ndcg_at_10']:.4f} R@10={r_or['recall_at_10']:.4f} "
              f"p50={r_or['latency_p50_ms']}ms")

        # 3. OR query attempting <@> with OR-formatted input
        if has_bm25:
            print(f"  [OR <@>] Evaluating...")
            r_or_wand = evaluate(
                lambda q, c=conn, t=table: search_bm25_or_wand(c, t, q),
                queries
            )
            results[f"5T_{ds_label}_or_wand"] = {**r_or_wand, "method": "OR tsquery + <@> (WAND attempt)"}
            print(f"    → NDCG={r_or_wand['ndcg_at_10']:.4f} R@10={r_or_wand['recall_at_10']:.4f} "
                  f"p50={r_or_wand['latency_p50_ms']}ms")

        # QPS for best OR method
        print(f"  [QPS] OR ts_rank_cd...")
        qps = measure_qps(
            lambda q, c=conn, t=table: search_bm25_or_tsrank(c, t, q),
            queries
        )
        results[f"5T_{ds_label}_qps"] = {f"qps_{k}": v for k, v in qps.items()}

        conn.close()

    return results


# ============================================================================
# Experiment 5-B: pl/pgsql BM25 (baseline + optimized)
# ============================================================================

def setup_inverted_index(conn, src_table: str, inv_table: str = "inverted_index",
                         config: str = "public.korean") -> float:
    """Build inverted index from source table. Returns build time in seconds."""
    with conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {inv_table} CASCADE")
        cur.execute(f"""
            CREATE TABLE {inv_table} (
                term TEXT,
                doc_id TEXT,
                term_freq INT,
                doc_length INT,
                PRIMARY KEY (term, doc_id)
            )
        """)

        t0 = time.perf_counter()
        cur.execute(f"""
            INSERT INTO {inv_table} (term, doc_id, term_freq, doc_length)
            SELECT term, id, cnt,
                   SUM(cnt) OVER (PARTITION BY id) AS doc_length
            FROM (
                SELECT
                    id,
                    (ts_row).lexeme AS term,
                    array_length((ts_row).positions, 1) AS cnt
                FROM (
                    SELECT id, unnest(to_tsvector('{config}', text)) AS ts_row
                    FROM {src_table}
                ) sub
            ) agg
        """)
        build_sec = round(time.perf_counter() - t0, 2)

        cur.execute(f"CREATE INDEX idx_{inv_table}_term ON {inv_table}(term)")
        cur.execute(f"CREATE INDEX idx_{inv_table}_docid ON {inv_table}(doc_id)")
    conn.commit()
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {inv_table}")
        n_rows = cur.fetchone()[0]
    print(f"  [{inv_table}] built: {n_rows} rows in {build_sec}s")
    return build_sec


def create_bm25_ranking_v1(conn, inv_table: str = "inverted_index",
                           config: str = "public.korean") -> None:
    """Create bm25_ranking() — original with full scan for AVG/COUNT."""
    with conn.cursor() as cur:
        cur.execute(f"""
            CREATE OR REPLACE FUNCTION bm25_ranking_v1(
                query TEXT, k1 FLOAT DEFAULT 1.2, b FLOAT DEFAULT 0.75
            )
            RETURNS TABLE(doc_id TEXT, score FLOAT) AS $$
            DECLARE
                avgdl FLOAT;
                total_docs INT;
            BEGIN
                SELECT AVG(i.doc_length) INTO avgdl FROM {inv_table} i;
                SELECT COUNT(DISTINCT i.doc_id) INTO total_docs FROM {inv_table} i;

                RETURN QUERY
                SELECT
                    i.doc_id,
                    SUM(
                        LOG((total_docs - df.df + 0.5) / (df.df + 0.5) + 1) *
                        (i.term_freq * (k1 + 1)) /
                        (i.term_freq + k1 * (1 - b + b * (i.doc_length::float / avgdl)))
                    )::FLOAT AS score
                FROM {inv_table} i
                JOIN (
                    SELECT inv.term, COUNT(DISTINCT inv.doc_id)::INT AS df
                    FROM {inv_table} inv
                    WHERE inv.term = ANY(tsvector_to_array(to_tsvector('{config}', query)))
                    GROUP BY inv.term
                ) df ON i.term = df.term
                WHERE i.term = ANY(tsvector_to_array(to_tsvector('{config}', query)))
                GROUP BY i.doc_id
                ORDER BY score DESC;
            END;
            $$ LANGUAGE plpgsql;
        """)
    conn.commit()
    print(f"  bm25_ranking_v1() created (full scan)")


def create_bm25_stats_tables(conn, inv_table: str = "inverted_index") -> None:
    """Create bm25_stats and bm25_df tables, populate from inverted_index."""
    with conn.cursor() as cur:
        # Stats table (1 row)
        cur.execute("DROP TABLE IF EXISTS bm25_stats CASCADE")
        cur.execute("""
            CREATE TABLE bm25_stats (
                id INT PRIMARY KEY DEFAULT 1,
                total_docs INT NOT NULL,
                avg_doc_length FLOAT NOT NULL
            )
        """)
        cur.execute(f"""
            INSERT INTO bm25_stats (total_docs, avg_doc_length)
            SELECT
                COUNT(DISTINCT doc_id),
                AVG(doc_length)
            FROM (
                SELECT doc_id, MAX(doc_length) AS doc_length
                FROM {inv_table}
                GROUP BY doc_id
            ) per_doc
        """)

        # DF table
        cur.execute("DROP TABLE IF EXISTS bm25_df CASCADE")
        cur.execute(f"""
            CREATE TABLE bm25_df AS
            SELECT term, COUNT(DISTINCT doc_id)::INT AS df
            FROM {inv_table}
            GROUP BY term
        """)
        cur.execute("ALTER TABLE bm25_df ADD PRIMARY KEY (term)")
        cur.execute("CREATE INDEX idx_bm25_df_term ON bm25_df(term)")
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM bm25_stats")
        stats = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM bm25_df")
        n_terms = cur.fetchone()[0]
    print(f"  bm25_stats: total_docs={stats[1]}, avg_dl={stats[2]:.1f}")
    print(f"  bm25_df: {n_terms} terms")


def create_bm25_ranking_v2(conn, inv_table: str = "inverted_index",
                           config: str = "public.korean") -> None:
    """Create bm25_ranking_v2() — optimized with stats/df table lookup."""
    with conn.cursor() as cur:
        cur.execute(f"""
            CREATE OR REPLACE FUNCTION bm25_ranking_v2(
                query TEXT, k1 FLOAT DEFAULT 1.2, b FLOAT DEFAULT 0.75
            )
            RETURNS TABLE(doc_id TEXT, score FLOAT) AS $$
            DECLARE
                avgdl FLOAT;
                total_docs INT;
            BEGIN
                SELECT s.total_docs, s.avg_doc_length
                INTO total_docs, avgdl
                FROM bm25_stats s WHERE s.id = 1;

                RETURN QUERY
                SELECT
                    i.doc_id,
                    SUM(
                        LOG((total_docs - df.df + 0.5) / (df.df + 0.5) + 1) *
                        (i.term_freq * (k1 + 1)) /
                        (i.term_freq + k1 * (1 - b + b * (i.doc_length::float / avgdl)))
                    )::FLOAT AS score
                FROM {inv_table} i
                JOIN bm25_df df ON i.term = df.term
                WHERE i.term = ANY(tsvector_to_array(to_tsvector('{config}', query)))
                GROUP BY i.doc_id
                ORDER BY score DESC;
            END;
            $$ LANGUAGE plpgsql;
        """)
    conn.commit()
    print(f"  bm25_ranking_v2() created (stats table optimized)")


def create_bm25_incremental_trigger(conn, src_table: str,
                                    inv_table: str = "inverted_index",
                                    config: str = "public.korean") -> None:
    """Create trigger for incremental inverted_index + stats update on INSERT."""
    with conn.cursor() as cur:
        cur.execute(f"""
            CREATE OR REPLACE FUNCTION trigger_index_and_stats()
            RETURNS trigger AS $$
            DECLARE
                new_doc_length INT;
            BEGIN
                -- Delete old entries if updating
                DELETE FROM {inv_table} WHERE doc_id = NEW.id::TEXT;

                -- Insert new terms
                INSERT INTO {inv_table} (term, doc_id, term_freq, doc_length)
                SELECT term, NEW.id::TEXT, cnt,
                       SUM(cnt) OVER () AS doc_length
                FROM (
                    SELECT unnested AS term,
                           COUNT(*) AS cnt
                    FROM (
                        SELECT unnest(tsvector_to_array(
                            to_tsvector('{config}', NEW.text)
                        )) AS unnested
                    ) t
                    GROUP BY unnested
                ) agg;

                -- Get new doc length
                SELECT MAX(doc_length) INTO new_doc_length
                FROM {inv_table} WHERE doc_id = NEW.id::TEXT;

                -- Update bm25_df: increment df for each term
                INSERT INTO bm25_df (term, df)
                SELECT term, 1 FROM {inv_table} WHERE doc_id = NEW.id::TEXT
                ON CONFLICT (term) DO UPDATE SET df = bm25_df.df + 1;

                -- Update bm25_stats
                UPDATE bm25_stats SET
                    total_docs = total_docs + 1,
                    avg_doc_length = (avg_doc_length * total_docs + COALESCE(new_doc_length, 0))
                                     / (total_docs + 1)
                WHERE id = 1;

                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """)
        cur.execute(f"DROP TRIGGER IF EXISTS trg_phase5_index ON {src_table}")
        cur.execute(f"""
            CREATE TRIGGER trg_phase5_index
            AFTER INSERT ON {src_table}
            FOR EACH ROW EXECUTE FUNCTION trigger_index_and_stats()
        """)
    conn.commit()
    print(f"  Incremental trigger created on {src_table}")


def search_bm25_v1(conn, query_text: str, k: int = 10) -> List[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT doc_id FROM bm25_ranking_v1(%s) LIMIT %s", (query_text, k))
        return [str(r[0]) for r in cur.fetchall()]


def search_bm25_v2(conn, query_text: str, k: int = 10) -> List[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT doc_id FROM bm25_ranking_v2(%s) LIMIT %s", (query_text, k))
        return [str(r[0]) for r in cur.fetchall()]


def run_experiment_5B(db_url: str, miracl_docs, miracl_queries,
                      ezis_docs, ezis_queries) -> Dict:
    """Experiment 5-B: pl/pgsql BM25 baseline vs stats-optimized."""
    print("\n" + "=" * 70)
    print("Experiment 5-B: pl/pgsql BM25 (v1 full-scan vs v2 stats-optimized)")
    print("=" * 70)

    results = {}

    for ds_label, docs, queries, table in [
        ("miracl", miracl_docs, miracl_queries, "phase5_b_miracl"),
        ("ezis", ezis_docs, ezis_queries, "phase5_b_ezis"),
    ]:
        print(f"\n--- {ds_label.upper()} ---")
        conn = get_conn(db_url)
        setup_phase5_table(conn, table, docs)

        inv_table = f"inv_{ds_label}"

        # Build inverted index
        build_sec = setup_inverted_index(conn, table, inv_table)
        results[f"5B_{ds_label}_build_sec"] = build_sec

        # --- v1: full scan ---
        create_bm25_ranking_v1(conn, inv_table)

        print(f"  [v1] Evaluating...")
        r_v1 = evaluate(lambda q, c=conn: search_bm25_v1(c, q), queries)
        results[f"5B_{ds_label}_v1"] = {**r_v1, "method": "pl/pgsql BM25 v1 (full scan)"}
        print(f"    → NDCG={r_v1['ndcg_at_10']:.4f} R@10={r_v1['recall_at_10']:.4f} "
              f"p50={r_v1['latency_p50_ms']}ms")

        # EXPLAIN ANALYZE for v1
        print(f"  [v1] EXPLAIN ANALYZE:")
        with conn.cursor() as cur:
            cur.execute(f"EXPLAIN ANALYZE SELECT * FROM bm25_ranking_v1(%s) LIMIT 10",
                        (queries[0]["text"],))
            for row in cur.fetchall():
                print(f"    {row[0]}")

        # QPS v1
        print(f"  [v1] QPS measurement...")
        qps_v1 = measure_qps(lambda q, c=conn: search_bm25_v1(c, q), queries)
        results[f"5B_{ds_label}_v1_qps"] = {f"qps_{k}": v for k, v in qps_v1.items()}

        # --- v2: stats table optimized ---
        create_bm25_stats_tables(conn, inv_table)
        create_bm25_ranking_v2(conn, inv_table)

        print(f"  [v2] Evaluating...")
        r_v2 = evaluate(lambda q, c=conn: search_bm25_v2(c, q), queries)
        results[f"5B_{ds_label}_v2"] = {**r_v2, "method": "pl/pgsql BM25 v2 (stats optimized)"}
        print(f"    → NDCG={r_v2['ndcg_at_10']:.4f} R@10={r_v2['recall_at_10']:.4f} "
              f"p50={r_v2['latency_p50_ms']}ms")

        # Verify NDCG identical
        if abs(r_v1["ndcg_at_10"] - r_v2["ndcg_at_10"]) > 0.001:
            print(f"  *** WARNING: v1/v2 NDCG mismatch! v1={r_v1['ndcg_at_10']} v2={r_v2['ndcg_at_10']}")

        # EXPLAIN ANALYZE for v2
        print(f"  [v2] EXPLAIN ANALYZE:")
        with conn.cursor() as cur:
            cur.execute(f"EXPLAIN ANALYZE SELECT * FROM bm25_ranking_v2(%s) LIMIT 10",
                        (queries[0]["text"],))
            for row in cur.fetchall():
                print(f"    {row[0]}")

        # QPS v2
        print(f"  [v2] QPS measurement...")
        qps_v2 = measure_qps(lambda q, c=conn: search_bm25_v2(c, q), queries)
        results[f"5B_{ds_label}_v2_qps"] = {f"qps_{k}": v for k, v in qps_v2.items()}

        # --- Incremental insert test ---
        print(f"  [v2] Incremental insert test (trigger)...")
        create_bm25_incremental_trigger(conn, table, inv_table)
        test_doc = {"id": "incr_test_001", "text": "한국어 검색 엔진 테스트 문서입니다."}
        with conn.cursor() as cur:
            t0 = time.perf_counter()
            cur.execute(f"INSERT INTO {table} (id, text, tsv) VALUES (%s, %s, to_tsvector('public.korean', %s))",
                        (test_doc["id"], test_doc["text"], test_doc["text"]))
            conn.commit()
            insert_ms = round((time.perf_counter() - t0) * 1000, 2)
        print(f"    Incremental insert: {insert_ms}ms")
        results[f"5B_{ds_label}_incremental_insert_ms"] = insert_ms

        # Cleanup test doc
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM {inv_table} WHERE doc_id = %s", (test_doc["id"],))
            cur.execute(f"DELETE FROM {table} WHERE id = %s", (test_doc["id"],))
        conn.commit()

        conn.close()

    return results


# ============================================================================
# Experiment 5-A: pgvector-sparse IDF staleness
# ============================================================================

def run_experiment_5A(db_url: str, miracl_docs, miracl_queries,
                      ezis_docs, ezis_queries) -> Dict:
    """Experiment 5-A: pgvector-sparse BM25 IDF staleness quantification."""
    print("\n" + "=" * 70)
    print("Experiment 5-A: pgvector-sparse IDF staleness")
    print("=" * 70)

    from experiments.common.bm25_module import BM25Embedder_PG

    results = {}

    for ds_label, docs, queries in [
        ("miracl", miracl_docs, miracl_queries),
    ]:
        print(f"\n--- {ds_label.upper()} ---")
        conn = get_conn(db_url)
        register_vector(conn)

        n_total = len(docs)
        n_initial = int(n_total * 0.8)  # 80% for initial fit
        n_new = n_total - n_initial

        initial_docs = docs[:n_initial]
        new_docs = docs[n_initial:]

        print(f"  Split: {n_initial} initial + {n_new} new docs")

        # Fit on initial docs first to get vocab_size
        print(f"  Fitting BM25 on {n_initial} docs...")
        emb = BM25Embedder_PG(tokenizer="kiwi-cong")
        t0 = time.perf_counter()
        emb.fit([d["text"] for d in initial_docs])
        fit_sec = round(time.perf_counter() - t0, 1)
        print(f"  Fit done: {fit_sec}s, vocab={emb.vocab_size}")

        # Create sparse vector table with correct dimension
        table = f"phase5_a_{ds_label}"
        dim = emb.vocab_size
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
            cur.execute(f"""
                CREATE TABLE {table} (
                    id TEXT PRIMARY KEY,
                    sparse_vec sparsevec({dim})
                )
            """)
        conn.commit()

        # Embed and insert initial docs
        print(f"  Embedding + inserting initial {n_initial} docs...")
        t0 = time.perf_counter()
        with conn.cursor() as cur:
            for d in initial_docs:
                sv = emb.embed_document(d["text"])
                cur.execute(f"INSERT INTO {table} (id, sparse_vec) VALUES (%s, %s)",
                            (str(d["id"]), sv))
        conn.commit()
        embed_sec = round(time.perf_counter() - t0, 1)
        print(f"  Initial embed+insert: {embed_sec}s")

        # Baseline NDCG (initial only)
        def search_sparse(query_text: str) -> List[str]:
            qv = emb.embed_query(query_text)
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT id, sparse_vec <#> %s AS dist
                    FROM {table}
                    ORDER BY dist
                    LIMIT 10
                """, (qv,))
                return [str(r[0]) for r in cur.fetchall()]

        print(f"  [Baseline] Evaluating on initial {n_initial} docs...")
        r_baseline = evaluate(search_sparse, queries)
        results[f"5A_{ds_label}_baseline"] = {
            **r_baseline,
            "method": f"pgvector-sparse (initial {n_initial} docs)",
            "n_docs": n_initial,
            "fit_sec": fit_sec,
            "embed_sec": embed_sec,
        }
        print(f"    → NDCG={r_baseline['ndcg_at_10']:.4f}")

        # Add new docs WITHOUT rebuild (stale IDF)
        print(f"  [Stale] Adding {n_new} docs with stale IDF...")
        t0 = time.perf_counter()
        with conn.cursor() as cur:
            for d in new_docs:
                sv = emb.embed_document(d["text"])  # uses old IDF
                cur.execute(f"INSERT INTO {table} (id, sparse_vec) VALUES (%s, %s)",
                            (str(d["id"]), sv))
        conn.commit()
        stale_add_sec = round(time.perf_counter() - t0, 1)

        print(f"  [Stale] Evaluating with stale IDF...")
        r_stale = evaluate(search_sparse, queries)
        results[f"5A_{ds_label}_stale"] = {
            **r_stale,
            "method": f"pgvector-sparse (stale IDF, +{n_new} docs)",
            "n_docs": n_total,
            "add_sec": stale_add_sec,
        }
        ndcg_drop = r_baseline["ndcg_at_10"] - r_stale["ndcg_at_10"]
        print(f"    → NDCG={r_stale['ndcg_at_10']:.4f} (drop={ndcg_drop:.4f})")

        # Full rebuild
        print(f"  [Rebuild] Full rebuild on {n_total} docs...")
        emb2 = BM25Embedder_PG(tokenizer="kiwi-cong")
        t0 = time.perf_counter()
        emb2.fit([d["text"] for d in docs])
        fit2_sec = round(time.perf_counter() - t0, 1)

        # Recreate table with new vocab dimension if changed
        dim2 = emb2.vocab_size
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
            cur.execute(f"""
                CREATE TABLE {table} (
                    id TEXT PRIMARY KEY,
                    sparse_vec sparsevec({dim2})
                )
            """)
        conn.commit()

        t0 = time.perf_counter()
        with conn.cursor() as cur:
            for d in docs:
                sv = emb2.embed_document(d["text"])
                cur.execute(f"INSERT INTO {table} (id, sparse_vec) VALUES (%s, %s)",
                            (str(d["id"]), sv))
        conn.commit()
        rebuild_sec = round(time.perf_counter() - t0, 1)

        def search_sparse2(query_text: str) -> List[str]:
            qv = emb2.embed_query(query_text)
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT id, sparse_vec <#> %s AS dist
                    FROM {table}
                    ORDER BY dist
                    LIMIT 10
                """, (qv,))
                return [str(r[0]) for r in cur.fetchall()]

        print(f"  [Rebuild] Evaluating after full rebuild...")
        r_rebuild = evaluate(search_sparse2, queries)
        results[f"5A_{ds_label}_rebuild"] = {
            **r_rebuild,
            "method": f"pgvector-sparse (full rebuild, {n_total} docs)",
            "n_docs": n_total,
            "fit_sec": fit2_sec,
            "rebuild_sec": rebuild_sec,
        }
        print(f"    → NDCG={r_rebuild['ndcg_at_10']:.4f}")

        # Per-doc add latency
        test_doc = docs[0]
        latencies = []
        for _ in range(20):
            t0 = time.perf_counter()
            sv = emb.embed_document(test_doc["text"])
            latencies.append((time.perf_counter() - t0) * 1000)
        results[f"5A_{ds_label}_per_doc_embed_ms"] = round(statistics.median(latencies), 2)
        print(f"  Per-doc embed latency: {results[f'5A_{ds_label}_per_doc_embed_ms']}ms")

        # QPS
        print(f"  [QPS] pgvector-sparse...")
        qps = measure_qps(search_sparse2, queries)
        results[f"5A_{ds_label}_qps"] = {f"qps_{k}": v for k, v in qps.items()}

        conn.close()

    return results


# ============================================================================
# Experiment 5-C: Hybrid BM25 + Dense (Bayesian fusion)
# ============================================================================

def run_experiment_5C(db_url: str, miracl_docs, miracl_queries,
                      ezis_docs, ezis_queries,
                      best_bm25_method: str = "v2") -> Dict:
    """Experiment 5-C: Hybrid BM25 + BGE-M3 dense fusion."""
    print("\n" + "=" * 70)
    print("Experiment 5-C: Hybrid BM25 + BGE-M3 dense")
    print("=" * 70)

    from FlagEmbedding import BGEM3FlagModel
    print("  Loading BGE-M3 model...", end="", flush=True)
    t0 = time.perf_counter()
    model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True)
    print(f" done ({time.perf_counter()-t0:.1f}s)")

    results = {}

    for ds_label, docs, queries, table in [
        ("miracl", miracl_docs, miracl_queries, "phase5_c_miracl"),
        ("ezis", ezis_docs, ezis_queries, "phase5_c_ezis"),
    ]:
        print(f"\n--- {ds_label.upper()} ---")
        conn = get_conn(db_url)
        register_vector(conn)

        # Setup docs table with dense vectors
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
            cur.execute(f"""
                CREATE TABLE {table} (
                    id TEXT PRIMARY KEY,
                    text TEXT,
                    tsv tsvector,
                    dense_vec vector(1024)
                )
            """)
            psycopg2.extras.execute_batch(cur,
                f"INSERT INTO {table} (id, text) VALUES (%s, %s)",
                [(str(d["id"]), d["text"]) for d in docs],
                page_size=500)
            cur.execute(f"UPDATE {table} SET tsv = to_tsvector('public.korean', text)")
            cur.execute(f"CREATE INDEX idx_{table}_tsv ON {table} USING GIN(tsv)")
        conn.commit()

        # Encode dense vectors
        print(f"  Encoding {len(docs)} dense vectors...")
        t0 = time.perf_counter()
        batch_size = 32
        for i in range(0, len(docs), batch_size):
            batch = [d["text"] for d in docs[i:i+batch_size]]
            embs = model.encode(batch, return_dense=True, return_sparse=False,
                                return_colbert_vecs=False)["dense_vecs"]
            with conn.cursor() as cur:
                for j, emb_vec in enumerate(embs):
                    doc_id = str(docs[i+j]["id"])
                    cur.execute(f"UPDATE {table} SET dense_vec = %s WHERE id = %s",
                                (emb_vec.tolist(), doc_id))
            conn.commit()
            print(f"    {min(i+batch_size, len(docs))}/{len(docs)}", end="\r")
        dense_sec = round(time.perf_counter() - t0, 1)
        print(f"\n  Dense encoding: {dense_sec}s")

        # HNSW index on dense
        print(f"  Creating HNSW index...")
        t0 = time.perf_counter()
        with conn.cursor() as cur:
            cur.execute(f"""
                CREATE INDEX idx_{table}_hnsw ON {table}
                USING hnsw(dense_vec vector_cosine_ops)
                WITH (m=16, ef_construction=200)
            """)
        conn.commit()
        hnsw_sec = round(time.perf_counter() - t0, 1)
        print(f"  HNSW index: {hnsw_sec}s")

        # Build inverted index for BM25
        inv_table = f"inv_c_{ds_label}"
        setup_inverted_index(conn, table, inv_table)
        create_bm25_stats_tables(conn, inv_table)
        create_bm25_ranking_v2(conn, inv_table)

        # RRF hybrid search
        def hybrid_rrf_search(query_text: str) -> List[str]:
            # BM25
            with conn.cursor() as cur:
                cur.execute("SELECT doc_id FROM bm25_ranking_v2(%s) LIMIT 20", (query_text,))
                bm25_top = [str(r[0]) for r in cur.fetchall()]

            # Dense
            q_emb = model.encode([query_text], return_dense=True, return_sparse=False,
                                 return_colbert_vecs=False)["dense_vecs"][0]
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT id FROM {table}
                    ORDER BY dense_vec <=> %s::vector
                    LIMIT 20
                """, (q_emb.tolist(),))
                dense_top = [str(r[0]) for r in cur.fetchall()]

            # RRF merge
            scores: Dict[str, float] = {}
            for ranked in [bm25_top, dense_top]:
                for rank, doc_id in enumerate(ranked):
                    scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (60 + rank + 1)
            return [d for d, _ in sorted(scores.items(), key=lambda x: -x[1])][:10]

        print(f"  [Hybrid RRF] Evaluating...")
        r_rrf = evaluate(hybrid_rrf_search, queries)
        results[f"5C_{ds_label}_rrf"] = {**r_rrf, "method": "Hybrid RRF (BM25v2 + BGE-M3 dense)"}
        print(f"    → NDCG={r_rrf['ndcg_at_10']:.4f} R@10={r_rrf['recall_at_10']:.4f} "
              f"p50={r_rrf['latency_p50_ms']}ms")

        # QPS
        print(f"  [Hybrid QPS]...")
        qps = measure_qps(hybrid_rrf_search, queries, concurrency_levels=[1, 4, 8])
        results[f"5C_{ds_label}_qps"] = {f"qps_{k}": v for k, v in qps.items()}

        results[f"5C_{ds_label}_dense_encode_sec"] = dense_sec
        results[f"5C_{ds_label}_hnsw_build_sec"] = hnsw_sec

        conn.close()

    return results


# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Phase 5: Production PG BM25 benchmark")
    parser.add_argument("--db-url", default="postgresql://postgres:postgres@localhost:5432/dev")
    parser.add_argument("--output-dir", default="results/phase5")
    parser.add_argument("--experiments", default="all",
                        help="Comma-separated: 5T,5B,5A,5C or 'all'")
    parser.add_argument("--miracl-10k", action="store_true",
                        help="Load 10K MIRACL docs from text_embedding table (--db-url)")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    exps = args.experiments.lower().split(",") if args.experiments != "all" else ["5t", "5b", "5a", "5c"]

    miracl_docs, miracl_queries, ezis_docs, ezis_queries = load_data()
    if args.miracl_10k:
        miracl_docs = load_miracl_from_db(args.db_url)

    all_results = {}

    if "5t" in exps:
        r = run_experiment_5T(args.db_url, miracl_docs, miracl_queries, ezis_docs, ezis_queries)
        all_results.update(r)

    if "5b" in exps:
        r = run_experiment_5B(args.db_url, miracl_docs, miracl_queries, ezis_docs, ezis_queries)
        all_results.update(r)

    if "5a" in exps:
        r = run_experiment_5A(args.db_url, miracl_docs, miracl_queries, ezis_docs, ezis_queries)
        all_results.update(r)

    if "5c" in exps:
        r = run_experiment_5C(args.db_url, miracl_docs, miracl_queries, ezis_docs, ezis_queries)
        all_results.update(r)

    # Save results
    out_path = out_dir / "phase5_production_pg.json"
    out_path.write_text(json.dumps(all_results, ensure_ascii=False, indent=2, default=str))
    print(f"\n[Done] Results saved to {out_path}")

    # Print summary table
    print("\n" + "=" * 90)
    print("Phase 5 Summary")
    print("=" * 90)
    print(f"{'Method':<45} {'NDCG@10':>8} {'R@10':>8} {'p50ms':>7} {'p95ms':>7}")
    print("-" * 90)
    for key, val in all_results.items():
        if isinstance(val, dict) and "ndcg_at_10" in val:
            name = val.get("method", key)
            print(f"  {name:<43} {val['ndcg_at_10']:>8.4f} {val['recall_at_10']:>8.4f} "
                  f"{val['latency_p50_ms']:>7.1f} {val['latency_p95_ms']:>7.1f}")


if __name__ == "__main__":
    main()
