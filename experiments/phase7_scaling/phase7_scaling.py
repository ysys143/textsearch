"""
Phase 7: PostgreSQL 3-way Scaling Comparison
  - pg_textsearch AND (<@> operator, bm25 index)
  - pg_textsearch OR  (GIN + ts_rank_cd)
  - VectorChord-BM25  (loaded from Phase 6-3 results)
  - pl/pgsql BM25 v2  (loaded from Phase 6-3 results)

Scales: 1K / 10K / 100K docs (MIRACL-ko corpus)

Usage:
  uv run python3 experiments/phase7_scaling/phase7_scaling.py \\
    --db-url postgresql://postgres:postgres@localhost:5432/dev \\
    --output-dir results/phase7
"""

import argparse
import json
import os
import time
from datetime import datetime
from typing import Dict, List

import psycopg2
import psycopg2.extras

QUERIES_PATH = "data/miracl/queries_dev.json"
P6_RESULTS_PATH = "results/phase6/phase6_3_scaling.json"
SCALES = [1_000, 10_000, 100_000]
N_QUERIES = 213


# ---------------------------------------------------------------------------
# Stored procedures: hybrid RRF + Bayesian (registered once, called per query)
# ---------------------------------------------------------------------------

def setup_hybrid_functions(conn):
    """Register p7_hybrid_rrf() and p7_hybrid_bayes() as PL/pgSQL functions."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE OR REPLACE FUNCTION p7_hybrid_rrf(
                tbl       TEXT,
                q_text    TEXT,
                q_emb     vector(128),
                k         INT DEFAULT 10
            ) RETURNS TABLE(id TEXT, score FLOAT) AS $fn$
            DECLARE sql TEXT;
            BEGIN
                sql := format($q$
                    WITH bm25 AS (
                        SELECT id,
                               ROW_NUMBER() OVER (ORDER BY text <@> %L) AS rk
                        FROM %I ORDER BY text <@> %L LIMIT 100
                    ),
                    dense AS (
                        SELECT id,
                               ROW_NUMBER() OVER (ORDER BY emb <=> %L) AS rk
                        FROM %I ORDER BY emb <=> %L LIMIT 100
                    )
                    SELECT COALESCE(b.id, d.id)::text,
                           COALESCE(1.0/(60+b.rk),0) + COALESCE(1.0/(60+d.rk),0)
                    FROM bm25 b
                    FULL OUTER JOIN dense d ON b.id = d.id
                    ORDER BY 2 DESC LIMIT %s
                $q$,
                q_text, tbl, q_text,
                q_emb::text, tbl, q_emb::text,
                k);
                RETURN QUERY EXECUTE sql;
            END;
            $fn$ LANGUAGE plpgsql;
        """)
        cur.execute("""
            CREATE OR REPLACE FUNCTION p7_hybrid_bayes(
                tbl       TEXT,
                q_text    TEXT,
                q_emb     vector(128),
                k         INT DEFAULT 10
            ) RETURNS TABLE(id TEXT, score FLOAT) AS $fn$
            DECLARE sql TEXT;
            BEGIN
                sql := format($q$
                    WITH bm25_raw AS (
                        SELECT id, -(text <@> %L) AS score
                        FROM %I ORDER BY text <@> %L LIMIT 100
                    ),
                    dense_raw AS (
                        SELECT id, 1 - (emb <=> %L) AS score
                        FROM %I ORDER BY emb <=> %L LIMIT 100
                    ),
                    bm25_p95 AS (
                        SELECT percentile_cont(0.95)
                               WITHIN GROUP (ORDER BY score) AS p95
                        FROM bm25_raw
                    ),
                    dense_p95 AS (
                        SELECT percentile_cont(0.95)
                               WITHIN GROUP (ORDER BY score) AS p95
                        FROM dense_raw
                    ),
                    bm25_prob AS (
                        SELECT b.id,
                               1.0 / (1 + exp(-(b.score - p.p95))) AS prob
                        FROM bm25_raw b, bm25_p95 p
                    ),
                    dense_prob AS (
                        SELECT d.id,
                               1.0 / (1 + exp(-(d.score - p.p95))) AS prob
                        FROM dense_raw d, dense_p95 p
                    ),
                    fused AS (
                        SELECT COALESCE(b.id, d.id)::text AS id,
                               COALESCE(ln(GREATEST(b.prob,1e-6)
                                        / GREATEST(1-b.prob,1e-6)), 0)
                             + COALESCE(ln(GREATEST(d.prob,1e-6)
                                        / GREATEST(1-d.prob,1e-6)), 0) AS log_odds
                        FROM bm25_prob b
                        FULL OUTER JOIN dense_prob d ON b.id = d.id
                    )
                    SELECT id, 1.0/(1+exp(-log_odds))::float
                    FROM fused ORDER BY log_odds DESC LIMIT %s
                $q$,
                q_text, tbl, q_text,
                q_emb::text, tbl, q_emb::text,
                k);
                RETURN QUERY EXECUTE sql;
            END;
            $fn$ LANGUAGE plpgsql;
        """)
    conn.commit()
    print("  [OK] p7_hybrid_rrf, p7_hybrid_bayes registered")


# ---------------------------------------------------------------------------
# Helpers (same patterns as phase6_3_scaling.py)
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


def load_corpus_from_db(conn) -> List[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT id, text FROM text_embedding ORDER BY id")
        rows = cur.fetchall()
    return [{"id": str(r[0]), "text": r[1]} for r in rows]


def scale_corpus(base_docs: List[dict], target_size: int) -> List[dict]:
    """Replicate base docs with unique IDs to reach target_size."""
    result = []
    i = 0
    while len(result) < target_size:
        doc = base_docs[i % len(base_docs)]
        suffix = i // len(base_docs)
        new_id = f"{doc['id']}_{suffix}" if suffix > 0 else str(doc['id'])
        result.append({"id": new_id, "text": doc["text"]})
        i += 1
    return result[:target_size]


def measure_latency(fn, queries: List[dict], warmup: int = 5) -> dict:
    # Warm the relevant indexes independently before measuring
    for q in queries[:warmup]:
        fn(q["text"])
    latencies = []
    for q in queries:
        t0 = time.perf_counter()
        fn(q["text"])
        latencies.append((time.perf_counter() - t0) * 1000)
    latencies.sort()
    n = len(latencies)
    return {
        "p50": round(latencies[n // 2], 2),
        "p95": round(latencies[int(n * 0.95)], 2),
        "p99": round(latencies[int(n * 0.99)], 2),
    }


# ---------------------------------------------------------------------------
# pg_textsearch scaling
# ---------------------------------------------------------------------------

def run_pgsearch_scale(conn, docs: List[dict], queries: List[dict],
                       scale: int) -> dict:
    table = f"t7_pgsearch_{scale // 1000}k"
    bm25_idx = f"idx_{table}_bm25"
    gin_idx = f"idx_{table}_gin"

    # --- Setup ---
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS pg_textsearch")
    conn.commit()

    with conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        cur.execute(f"""
            CREATE TABLE {table} (
                id  TEXT PRIMARY KEY,
                text TEXT,
                tsv  tsvector,
                emb  vector(128)
            )
        """)
    conn.commit()

    # --- Insert ---
    t0 = time.perf_counter()
    psycopg2.extras.execute_batch(
        conn.cursor(),
        f"INSERT INTO {table} (id, text) VALUES (%s, %s)",
        [(d["id"], d["text"]) for d in docs], page_size=500)
    conn.commit()
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE {table} SET tsv = to_tsvector('public.korean', text)")
        # copy vectors from text_embedding (base ID = numeric part before '_')
        cur.execute(f"""
            UPDATE {table} t
            SET emb = te.embedding
            FROM text_embedding te
            WHERE SPLIT_PART(t.id, '_', 1)::int = te.id
        """)
    conn.commit()
    insert_sec = round(time.perf_counter() - t0, 2)
    print(f"    insert+tsv+emb: {scale} docs in {insert_sec}s"
          f" ({scale / insert_sec:.0f} docs/sec)")

    # --- BM25 index (pg_textsearch) ---
    t0 = time.perf_counter()
    with conn.cursor() as cur:
        cur.execute(f"DROP INDEX IF EXISTS {bm25_idx}")
        cur.execute(f"""
            CREATE INDEX {bm25_idx}
            ON {table}
            USING bm25(text)
            WITH (text_config='public.korean')
        """)
    conn.commit()
    bm25_idx_sec = round(time.perf_counter() - t0, 2)
    print(f"    bm25 index build: {bm25_idx_sec}s")

    # --- GIN index for OR queries ---
    t0 = time.perf_counter()
    with conn.cursor() as cur:
        cur.execute(f"DROP INDEX IF EXISTS {gin_idx}")
        cur.execute(f"CREATE INDEX {gin_idx} ON {table} USING GIN(tsv)")
    conn.commit()
    gin_idx_sec = round(time.perf_counter() - t0, 2)
    print(f"    gin index build: {gin_idx_sec}s")

    # --- HNSW index for dense hybrid ---
    hnsw_idx = f"idx_{table}_hnsw"
    t0 = time.perf_counter()
    with conn.cursor() as cur:
        cur.execute(f"DROP INDEX IF EXISTS {hnsw_idx}")
        cur.execute(f"""
            CREATE INDEX {hnsw_idx} ON {table}
            USING hnsw (emb vector_cosine_ops)
        """)
    conn.commit()
    hnsw_idx_sec = round(time.perf_counter() - t0, 2)
    print(f"    hnsw index build: {hnsw_idx_sec}s")

    # --- Index sizes ---
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT pg_size_pretty(pg_relation_size('{bm25_idx}'))")
        bm25_size = cur.fetchone()[0]
        cur.execute(
            f"SELECT pg_size_pretty(pg_relation_size('{gin_idx}'))")
        gin_size = cur.fetchone()[0]
        cur.execute(
            f"SELECT pg_size_pretty(pg_relation_size('{hnsw_idx}'))")
        hnsw_size = cur.fetchone()[0]
        cur.execute(
            f"SELECT pg_size_pretty(pg_total_relation_size('{table}'))")
        total_size = cur.fetchone()[0]
    print(f"    bm25={bm25_size}  gin={gin_size}  hnsw={hnsw_size}  total={total_size}")

    # --- AND latency (<@> operator) ---
    def search_and(query_text: str):
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id FROM {table} ORDER BY text <@> %s LIMIT 10",
                (query_text,))
            return [r[0] for r in cur.fetchall()]

    lat_and = measure_latency(search_and, queries)
    print(f"    AND latency: p50={lat_and['p50']}ms p95={lat_and['p95']}ms")

    # --- OR latency (GIN + ts_rank_cd) ---
    def search_or(query_text: str):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tsvector_to_array(to_tsvector('public.korean', %s))",
                (query_text,))
            tokens = cur.fetchone()[0]
            if not tokens:
                return []
            or_query = " | ".join(tokens)
            cur.execute(f"""
                SELECT id, ts_rank_cd(tsv, to_tsquery('public.korean', %s)) AS score
                FROM {table}
                WHERE tsv @@ to_tsquery('public.korean', %s)
                ORDER BY score DESC
                LIMIT 10
            """, (or_query, or_query))
            return [r[0] for r in cur.fetchall()]

    lat_or = measure_latency(search_or, queries)
    print(f"    OR  latency: p50={lat_or['p50']}ms p95={lat_or['p95']}ms")

    # --- Hybrid RRF + Bayesian via registered procedures ---
    def get_query_emb(query_text: str):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT embedding FROM text_embedding WHERE text = %s LIMIT 1",
                (query_text,))
            row = cur.fetchone()
            return row[0] if row else None

    def search_rrf(query_text: str):
        q_emb = get_query_emb(query_text)
        if not q_emb:
            return []
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM p7_hybrid_rrf(%s, %s, %s)",
                (table, query_text, q_emb))
            return [r[0] for r in cur.fetchall()]

    def search_bayesian(query_text: str):
        q_emb = get_query_emb(query_text)
        if not q_emb:
            return []
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM p7_hybrid_bayes(%s, %s, %s)",
                (table, query_text, q_emb))
            return [r[0] for r in cur.fetchall()]

    lat_rrf = measure_latency(search_rrf, queries)
    print(f"    RRF   latency: p50={lat_rrf['p50']}ms p95={lat_rrf['p95']}ms")

    lat_bayes = measure_latency(search_bayesian, queries)
    print(f"    Bayes latency: p50={lat_bayes['p50']}ms p95={lat_bayes['p95']}ms")

    return {
        "scale": scale,
        "insert_sec": insert_sec,
        "bm25_index_build_sec": bm25_idx_sec,
        "gin_index_build_sec": gin_idx_sec,
        "hnsw_index_build_sec": hnsw_idx_sec,
        "bm25_index_size": bm25_size,
        "gin_index_size": gin_size,
        "hnsw_index_size": hnsw_size,
        "total_size": total_size,
        "and_latency_p50": lat_and["p50"],
        "and_latency_p95": lat_and["p95"],
        "and_latency_p99": lat_and["p99"],
        "or_latency_p50": lat_or["p50"],
        "or_latency_p95": lat_or["p95"],
        "or_latency_p99": lat_or["p99"],
        "rrf_latency_p50": lat_rrf["p50"],
        "rrf_latency_p95": lat_rrf["p95"],
        "rrf_latency_p99": lat_rrf["p99"],
        "bayes_latency_p50": lat_bayes["p50"],
        "bayes_latency_p95": lat_bayes["p95"],
        "bayes_latency_p99": lat_bayes["p99"],
    }


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(pgsearch_results: List[dict], p6_data: dict,
                    output_dir: str):
    vchord = {r["scale"]: r for r in p6_data["vchord"]}
    plpgsql = {r["scale"]: r for r in p6_data["plpgsql"]}
    pgsearch = {r["scale"]: r for r in pgsearch_results}

    lines = [
        "# Phase 7: PostgreSQL Scaling Comparison",
        "",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "---",
        "",
        "## Latency p50 (ms)",
        "",
        "| Scale | BM25 AND | BM25+Dense RRF | BM25+Dense Bayes | BM25 OR | VectorChord | pl/pgsql |",
        "|-------|----------|---------------|-----------------|---------|-------------|---------|",
    ]

    for scale in SCALES:
        label = f"{scale // 1000}K"
        ps = pgsearch.get(scale, {})
        vc = vchord.get(scale, {})
        pl = plpgsql.get(scale, {})
        lines.append(
            f"| {label:>5} "
            f"| {ps.get('and_latency_p50', '?'):>6}ms "
            f"| {ps.get('rrf_latency_p50', '?'):>8}ms "
            f"| {ps.get('bayes_latency_p50', '?'):>8}ms "
            f"| {ps.get('or_latency_p50', '?'):>6}ms "
            f"| {vc.get('latency_p50', '?'):>6}ms "
            f"| {pl.get('latency_p50', '?'):>6}ms |"
        )

    lines += [
        "",
        "## Latency p95 (ms)",
        "",
        "| Scale | pg_textsearch AND | pg_textsearch OR | VectorChord-BM25 | pl/pgsql BM25 v2 |",
        "|-------|------------------|-----------------|-----------------|-----------------|",
    ]
    for scale in SCALES:
        label = f"{scale // 1000}K"
        ps = pgsearch.get(scale, {})
        vc = vchord.get(scale, {})
        pl = plpgsql.get(scale, {})
        lines.append(
            f"| {label:>5} "
            f"| {ps.get('and_latency_p95', '?'):>7}ms "
            f"| {ps.get('or_latency_p95', '?'):>7}ms "
            f"| {vc.get('latency_p95', '?'):>7}ms "
            f"| {pl.get('latency_p95', '?'):>7}ms |"
        )

    lines += [
        "",
        "## Index Build Time (s)",
        "",
        "| Scale | pg_textsearch bm25 | pg_textsearch gin | VectorChord bm25 | pl/pgsql inv |",
        "|-------|-------------------|------------------|-----------------|-------------|",
    ]
    for scale in SCALES:
        label = f"{scale // 1000}K"
        ps = pgsearch.get(scale, {})
        vc = vchord.get(scale, {})
        pl = plpgsql.get(scale, {})
        lines.append(
            f"| {label:>5} "
            f"| {ps.get('bm25_index_build_sec', '?'):>6}s "
            f"| {ps.get('gin_index_build_sec', '?'):>6}s "
            f"| {vc.get('index_build_sec', '?'):>6}s "
            f"| {pl.get('index_build_sec', '?'):>6}s |"
        )

    lines += [
        "",
        "## Index Size",
        "",
        "| Scale | pg_textsearch bm25 | pg_textsearch gin | VectorChord | pl/pgsql inv |",
        "|-------|-------------------|------------------|------------|-------------|",
    ]
    for scale in SCALES:
        label = f"{scale // 1000}K"
        ps = pgsearch.get(scale, {})
        vc = vchord.get(scale, {})
        pl = plpgsql.get(scale, {})
        lines.append(
            f"| {label:>5} "
            f"| {ps.get('bm25_index_size', '?'):>10} "
            f"| {ps.get('gin_index_size', '?'):>10} "
            f"| {vc.get('index_size', '?'):>10} "
            f"| {pl.get('inv_index_size', '?'):>10} |"
        )

    lines += [
        "",
        "---",
        "",
        "## 요약",
        "",
        "- **pg_textsearch AND**: BM25 인덱스, `<@>` 연산자 (AND matching)",
        "- **pg_textsearch OR**: GIN 인덱스 + ts_rank_cd (OR matching)",
        "- **VectorChord-BM25**: Block-WeakAnd posting list (Phase 6-3 실측)",
        "- **pl/pgsql BM25 v2**: B-tree 역인덱스, real-TF (Phase 6-3 실측)",
        "",
    ]

    os.makedirs(output_dir, exist_ok=True)
    report_path = os.path.join(output_dir, "phase7_scaling_report.md")
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
    parser.add_argument("--scales", nargs="+", type=int, default=SCALES)
    args = parser.parse_args()

    print("=" * 70)
    print("Phase 7: PostgreSQL 3-way Scaling Comparison")
    print("=" * 70)

    # Connect
    conn = connect(args.db_url, "main DB (port 5432)")
    if not conn:
        print("Cannot connect. Is the DB running?")
        return

    # Load data
    queries = load_queries()[:N_QUERIES]
    print(f"  Queries: {len(queries)}")

    base_docs = load_corpus_from_db(conn)
    print(f"  Corpus base: {len(base_docs)} docs")

    # Load Phase 6-3 results
    with open(P6_RESULTS_PATH, encoding="utf-8") as f:
        p6_data = json.load(f)
    print(f"  Phase 6-3 results loaded: vchord={len(p6_data['vchord'])}"
          f" plpgsql={len(p6_data['plpgsql'])}")

    # Register hybrid stored procedures
    setup_hybrid_functions(conn)

    # Run pg_textsearch scaling
    pgsearch_results = []
    for scale in args.scales:
        print(f"\n--- pg_textsearch @ {scale:,} docs ---")
        try:
            result = run_pgsearch_scale(conn, scale_corpus(base_docs, scale),
                                        queries, scale)
            pgsearch_results.append(result)
        except Exception as e:
            conn.rollback()
            print(f"  [ERROR] {e}")
            import traceback
            traceback.print_exc()

    conn.close()

    # Save JSON
    os.makedirs(args.output_dir, exist_ok=True)
    json_path = os.path.join(args.output_dir, "phase7_scaling.json")
    output = {
        "generated": datetime.now().isoformat(),
        "pgsearch": pgsearch_results,
        "vchord": p6_data["vchord"],
        "plpgsql": p6_data["plpgsql"],
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n  JSON: {json_path}")

    # Generate report
    generate_report(pgsearch_results, p6_data, args.output_dir)

    print("\nDone.")


if __name__ == "__main__":
    main()
