"""Phase 2: tsvector 한국어 통합 비교 벤치마크.

PostgreSQL 네이티브 FTS 파이프라인에 한국어 형태소 분석을 통합하는 모든 방법 비교.
두 가지 핵심 질문:
  1. PostgreSQL tsvector를 한국어에서 제대로 쓸 수 있는가?
  2. pg_textsearch(Timescale BM25)에 한국어 형태소 분석기를 접합할 수 있는가?

Methods:
  2-A: textsearch_ko (MeCab + mecab-ko-dic C extension) — ts_rank_cd
  2-B: plpython3u + kiwipiepy (custom tsvector using kiwi_tokenize) — ts_rank_cd
  2-C: korean_bigram (custom C parser, Korean syllable unigram) — ts_rank_cd
  2-D: ParadeDB pg_search (Tantivy-based BM25, port 5433) — BM25
  2-E: pg_tokenizer from scratch (Rust/pgrx) — skipped, requires Rust dev
  2-F: pgroonga (Groonga FTS, port 5435) — Groonga score
  2-G: pg_bigm (bigram GIN index, no morphology) — bigm_similarity
  2-H-a: pg_textsearch + public.korean (MeCab BM25) — BM25/WAND [핵심 실험]
  2-H-b: pg_textsearch + public.korean_bigram (C parser BM25) — BM25/WAND

Usage:
    uv run python3 experiments/phase2_tsvector/phase2_tsvector_comparison.py \\
        --db-url postgresql://postgres:postgres@localhost:5432/dev \\
        --paradedb-url postgresql://postgres:postgres@localhost:5433/dev \\
        --pgroonga-url postgresql://postgres:postgres@localhost:5435/dev \\
        --output-dir results/phase2
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

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

import psycopg2
import psycopg2.extras


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
    return hits / len(relevant_ids) if len(relevant_ids) > 0 else 0.0


def mrr_score(ranked_ids: List[str], relevant_ids: set) -> float:
    for rank, doc_id in enumerate(ranked_ids, start=1):
        if doc_id in relevant_ids:
            return 1.0 / rank
    return 0.0


# ---------------------------------------------------------------------------
# DB setup helpers
# ---------------------------------------------------------------------------

def setup_documents_table(conn, docs: List[Dict], table: str) -> None:
    """Create and populate a documents table."""
    with conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        cur.execute(f"""
            CREATE TABLE {table} (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL
            )
        """)
        psycopg2.extras.execute_values(
            cur,
            f"INSERT INTO {table} (id, text) VALUES %s",
            [(str(d["id"]), d["text"]) for d in docs],
            page_size=500,
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Method 2-A: textsearch_ko (MeCab + mecab-ko-dic)
# ---------------------------------------------------------------------------

def setup_textsearch_ko(conn, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_ts_config WHERE cfgname='korean' LIMIT 1")
        if not cur.fetchone():
            print("    [2-A] textsearch_ko 'korean' config not found")
            return False
        cur.execute("SELECT to_tsvector('korean', '형태소 분석 테스트')")
        result = cur.fetchone()[0]
        if not result:
            print("    [2-A] textsearch_ko 빈 결과 — mecab-ko-dic 미설치")
            return False

        cur.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS tsv_mecab")
        cur.execute(f"ALTER TABLE {table} ADD COLUMN tsv_mecab tsvector")
        t0 = time.perf_counter()
        cur.execute(f"UPDATE {table} SET tsv_mecab = to_tsvector('korean', text)")
        build_time = time.perf_counter() - t0
        cur.execute(f"DROP INDEX IF EXISTS idx_{table}_mecab")
        cur.execute(f"CREATE INDEX idx_{table}_mecab ON {table} USING gin(tsv_mecab)")
    conn.commit()
    print(f"    [2-A] index built in {build_time:.1f}s")
    return True


def search_textsearch_ko(conn, query_text: str, table: str, k: int = 10) -> List[str]:
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT id, ts_rank_cd(tsv_mecab, plainto_tsquery('korean', %s)) AS score
            FROM {table}
            WHERE tsv_mecab @@ plainto_tsquery('korean', %s)
            ORDER BY score DESC LIMIT %s
        """, (query_text, query_text, k))
        return [row[0] for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Method 2-B: plpython3u + kiwipiepy
# ---------------------------------------------------------------------------

def setup_plpython_tsvector(conn, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_proc WHERE proname='kiwi_tokenize' LIMIT 1")
        if not cur.fetchone():
            print("    [2-B] kiwi_tokenize not found")
            return False

        cur.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS tsv_kiwi")
        cur.execute(f"ALTER TABLE {table} ADD COLUMN tsv_kiwi tsvector")
        t0 = time.perf_counter()
        cur.execute(f"""
            UPDATE {table}
            SET tsv_kiwi = array_to_string(kiwi_tokenize(text), ' ')::tsvector
        """)
        build_time = time.perf_counter() - t0
        cur.execute(f"DROP INDEX IF EXISTS idx_{table}_kiwi")
        cur.execute(f"CREATE INDEX idx_{table}_kiwi ON {table} USING gin(tsv_kiwi)")
    conn.commit()
    print(f"    [2-B] index built in {build_time:.1f}s")
    return True


def search_plpython(conn, query_text: str, table: str, k: int = 10) -> List[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT kiwi_tokenize_query(%s)", (query_text,))
        row = cur.fetchone()
        tokens = row[0] if row else []
        if not tokens:
            cur.execute("SELECT kiwi_tokenize(%s)", (query_text,))
            row = cur.fetchone()
            tokens = row[0] if row else []
        if not tokens:
            return []
        and_quoted = " & ".join(f"'{t.replace(chr(39), '')}'" for t in tokens)
        cur.execute(f"""
            SELECT id, ts_rank_cd(tsv_kiwi, to_tsquery(%s)) AS score
            FROM {table}
            WHERE tsv_kiwi @@ to_tsquery(%s)
            ORDER BY score DESC LIMIT %s
        """, (and_quoted, and_quoted, k))
        rows = cur.fetchall()
        if rows:
            return [row[0] for row in rows]
        or_quoted = " | ".join(f"'{t.replace(chr(39), '')}'" for t in tokens)
        cur.execute(f"""
            SELECT id, ts_rank_cd(tsv_kiwi, to_tsquery(%s)) AS score
            FROM {table}
            WHERE tsv_kiwi @@ to_tsquery(%s)
            ORDER BY score DESC LIMIT %s
        """, (or_quoted, or_quoted, k))
        return [row[0] for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Method 2-C: korean_bigram (custom C parser — syllable unigram)
# ---------------------------------------------------------------------------

def setup_korean_bigram(conn, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_extension WHERE extname='korean_bigram' LIMIT 1")
        if not cur.fetchone():
            print("    [2-C] korean_bigram extension not installed")
            return False
        cur.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS tsv_kbg")
        cur.execute(f"ALTER TABLE {table} ADD COLUMN tsv_kbg tsvector")
        t0 = time.perf_counter()
        cur.execute(f"UPDATE {table} SET tsv_kbg = to_tsvector('korean_bigram', text)")
        build_time = time.perf_counter() - t0
        cur.execute(f"DROP INDEX IF EXISTS idx_{table}_kbg")
        cur.execute(f"CREATE INDEX idx_{table}_kbg ON {table} USING gin(tsv_kbg)")
    conn.commit()
    print(f"    [2-C] index built in {build_time:.1f}s")
    return True


def search_korean_bigram(conn, query_text: str, table: str, k: int = 10) -> List[str]:
    with conn.cursor() as cur:
        # plainto_tsquery handles raw text (tokenizes + ANDs); to_tsquery requires operator syntax
        cur.execute(f"""
            SELECT id, ts_rank_cd(tsv_kbg, plainto_tsquery('korean_bigram', %s)) AS score
            FROM {table}
            WHERE tsv_kbg @@ plainto_tsquery('korean_bigram', %s)
            ORDER BY score DESC LIMIT %s
        """, (query_text, query_text, k))
        return [row[0] for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Method 2-D: ParadeDB pg_search (Tantivy BM25)
# ---------------------------------------------------------------------------

def setup_paradedb(conn, table: str, docs: List[Dict]) -> bool:
    """Load docs into ParadeDB and create BM25 index."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_extension WHERE extname='pg_search' LIMIT 1")
            if not cur.fetchone():
                print("    [2-D] pg_search not available in ParadeDB")
                return False
            cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
            cur.execute(f"""
                CREATE TABLE {table} (
                    id TEXT PRIMARY KEY,
                    text TEXT NOT NULL
                )
            """)
            psycopg2.extras.execute_values(
                cur, f"INSERT INTO {table} (id, text) VALUES %s",
                [(str(d["id"]), d["text"]) for d in docs], page_size=500
            )
        conn.commit()

        t0 = time.perf_counter()
        with conn.cursor() as cur:
            cur.execute(f"""
                CREATE INDEX {table}_bm25_idx ON {table}
                USING bm25(id, text)
                WITH (key_field='id')
            """)
        conn.commit()
        print(f"    [2-D] BM25 index built in {time.perf_counter()-t0:.1f}s")
        return True
    except Exception as e:
        print(f"    [2-D] FAILED: {e}")
        conn.rollback()
        return False


def search_paradedb(conn, query_text: str, table: str, k: int = 10) -> List[str]:
    """BM25 search using ParadeDB pg_search."""
    try:
        with conn.cursor() as cur:
            # Use phrase query with individual terms
            terms = query_text.split()[:5]
            if not terms:
                return []
            # Build OR query
            query_parts = " OR ".join(f"text:{t}" for t in terms)
            cur.execute(f"""
                SELECT id, paradedb.score(id)
                FROM {table}
                WHERE {table} @@@ %s
                ORDER BY paradedb.score(id) DESC
                LIMIT %s
            """, (query_parts, k))
            return [row[0] for row in cur.fetchall()]
    except Exception:
        conn.rollback()
        return []


# ---------------------------------------------------------------------------
# Method 2-F: pgroonga (Groonga FTS)
# ---------------------------------------------------------------------------

def setup_pgroonga(conn, table: str, docs: List[Dict]) -> bool:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_available_extensions WHERE name='pgroonga' LIMIT 1")
            if not cur.fetchone():
                print("    [2-F] pgroonga not available")
                return False
            cur.execute("CREATE EXTENSION IF NOT EXISTS pgroonga")
            cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
            cur.execute(f"""
                CREATE TABLE {table} (
                    id TEXT PRIMARY KEY,
                    text TEXT NOT NULL
                )
            """)
            psycopg2.extras.execute_values(
                cur, f"INSERT INTO {table} (id, text) VALUES %s",
                [(str(d["id"]), d["text"]) for d in docs], page_size=500
            )
        conn.commit()

        t0 = time.perf_counter()
        with conn.cursor() as cur:
            cur.execute(f"CREATE INDEX {table}_pgroonga ON {table} USING pgroonga(text)")
        conn.commit()
        print(f"    [2-F] pgroonga index built in {time.perf_counter()-t0:.1f}s")
        return True
    except Exception as e:
        print(f"    [2-F] FAILED: {e}")
        return False


def search_pgroonga(conn, query_text: str, table: str, k: int = 10) -> List[str]:
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT id, pgroonga_score(tableoid, ctid) AS score
                FROM {table}
                WHERE text &@~ %s
                ORDER BY score DESC
                LIMIT %s
            """, (query_text, k))
            rows = cur.fetchall()
            if rows:
                return [row[0] for row in rows]
            # fallback: LIKE match
            tokens = query_text.split()[:3]
            if tokens:
                like_cond = " OR ".join(["text LIKE %s"] * len(tokens))
                cur.execute(f"""
                    SELECT id FROM {table}
                    WHERE {like_cond} LIMIT %s
                """, [f"%{t}%" for t in tokens] + [k])
                return [row[0] for row in cur.fetchall()]
        return []
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Method 2-G: pg_bigm
# ---------------------------------------------------------------------------

def setup_pg_bigm(conn, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_extension WHERE extname='pg_bigm' LIMIT 1")
        if not cur.fetchone():
            print("    [2-G] pg_bigm not installed")
            return False
        cur.execute(f"DROP INDEX IF EXISTS idx_{table}_bigm")
        t0 = time.perf_counter()
        cur.execute(f"CREATE INDEX idx_{table}_bigm ON {table} USING gin(text gin_bigm_ops)")
        build_time = time.perf_counter() - t0
    conn.commit()
    print(f"    [2-G] pg_bigm index built in {build_time:.1f}s")
    return True


def search_pg_bigm(conn, query_text: str, table: str, k: int = 10) -> List[str]:
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT id, bigm_similarity(text, %s) AS score
            FROM {table}
            WHERE text LIKE %s
            ORDER BY score DESC LIMIT %s
        """, (query_text, f"%{query_text}%", k))
        rows = cur.fetchall()
        if not rows:
            tokens = [t for t in query_text.split() if len(t) >= 2][:3]
            if not tokens:
                return []
            placeholders = " OR ".join(["text LIKE %s"] * len(tokens))
            like_args = [f"%{t}%" for t in tokens]
            cur.execute(f"""
                SELECT id, bigm_similarity(text, %s) AS score
                FROM {table}
                WHERE {placeholders}
                ORDER BY score DESC LIMIT %s
            """, [query_text] + like_args + [k])
            rows = cur.fetchall()
        return [row[0] for row in rows]


# ---------------------------------------------------------------------------
# Method 2-H: pg_textsearch (Timescale BM25, Block-Max WAND)
# ---------------------------------------------------------------------------

def setup_pg_textsearch(conn, table: str, ts_config: str, idx_suffix: str) -> bool:
    """Create a pg_textsearch BM25 index with the given text search config.

    ts_config must be schema-qualified (e.g. 'public.korean') because
    pg_textsearch's index builder ignores the session search_path.
    """
    idx_name = f"idx_{table}_pts_{idx_suffix}"
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_extension WHERE extname='pg_textsearch' LIMIT 1")
            if not cur.fetchone():
                print(f"    pg_textsearch not installed")
                return False
            # Verify the text search config exists
            schema, cfgname = (ts_config.split(".", 1) if "." in ts_config
                               else ("pg_catalog", ts_config))
            cur.execute(
                "SELECT 1 FROM pg_ts_config c JOIN pg_namespace n ON n.oid=c.cfgnamespace "
                "WHERE n.nspname=%s AND c.cfgname=%s LIMIT 1",
                (schema, cfgname)
            )
            if not cur.fetchone():
                print(f"    text search config '{ts_config}' not found — skipping")
                return False
            cur.execute(f"DROP INDEX IF EXISTS {idx_name}")
            t0 = time.perf_counter()
            cur.execute(f"""
                CREATE INDEX {idx_name} ON {table}
                USING bm25(text) WITH (text_config='{ts_config}')
            """)
        conn.commit()
        print(f"    BM25 index ({ts_config}) built in {time.perf_counter()-t0:.1f}s")
        return True
    except Exception as e:
        print(f"    FAILED ({ts_config}): {e}")
        conn.rollback()
        return False


def search_pg_textsearch(conn, query_text: str, table: str, k: int = 10) -> List[str]:
    """BM25 search using pg_textsearch <@> operator (returns negative score, ASC order)."""
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT id FROM {table}
                ORDER BY text <@> %s
                LIMIT %s
            """, (query_text, k))
            rows = cur.fetchall()
            return [row[0] for row in rows if row[0] is not None]
    except Exception:
        conn.rollback()
        return []


# ---------------------------------------------------------------------------
# Method 2-I: pl/pgsql custom BM25 (from scratch — pure SQL inverted index)
# ---------------------------------------------------------------------------

_PLPGSQL_BM25_SETUP = """
-- 역색인 (term, doc_id, tf)
CREATE TABLE IF NOT EXISTS {idx_table} (
    term   TEXT  NOT NULL,
    doc_id TEXT  NOT NULL,
    tf     FLOAT NOT NULL,
    PRIMARY KEY (term, doc_id)
);
CREATE INDEX IF NOT EXISTS {idx_table}_term_idx ON {idx_table}(term);

-- term별 문서빈도 (df) 사전 저장 — 검색 시 COUNT(DISTINCT) 대체
CREATE TABLE IF NOT EXISTS {df_table} (
    term TEXT  PRIMARY KEY,
    df   INT   NOT NULL DEFAULT 0
);

-- 문서별 길이 per-row 저장 — 검색 시 GROUP BY SUM(tf) 대체
CREATE TABLE IF NOT EXISTS {doclen_table} (
    doc_id  TEXT PRIMARY KEY,
    doc_len INT  NOT NULL DEFAULT 0
);

-- 전체 통계: sentinel row (id=1) 고정, UPDATE로만 갱신
CREATE TABLE IF NOT EXISTS {stats_table} (
    id          INT   PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    total_docs  INT   NOT NULL DEFAULT 0,
    len_sum     BIGINT NOT NULL DEFAULT 0,
    avg_doc_len FLOAT NOT NULL DEFAULT 1
);
INSERT INTO {stats_table}(id) VALUES (1) ON CONFLICT DO NOTHING;

-- 전체 재빌드 (초기 적재 전용)
CREATE OR REPLACE FUNCTION {fn_build}(src TEXT) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE rec RECORD;
BEGIN
    TRUNCATE {idx_table}, {df_table}, {doclen_table};
    UPDATE {stats_table} SET total_docs=0, len_sum=0, avg_doc_len=1 WHERE id=1;

    FOR rec IN EXECUTE format('SELECT id::text, text FROM %I', src) LOOP
        INSERT INTO {idx_table}(term, doc_id, tf)
        SELECT lexeme, rec.id, array_length(positions, 1)::float
        FROM unnest(to_tsvector('{ts_config}', rec.text))
        ON CONFLICT (term, doc_id) DO UPDATE SET tf = EXCLUDED.tf;

        INSERT INTO {doclen_table}(doc_id, doc_len)
        SELECT rec.id, COALESCE(SUM(array_length(positions, 1)), 0)
        FROM unnest(to_tsvector('{ts_config}', rec.text))
        ON CONFLICT (doc_id) DO UPDATE SET doc_len = EXCLUDED.doc_len;
    END LOOP;

    -- df: 빌드 완료 후 일괄 계산 (루프 내 증분보다 빠름)
    INSERT INTO {df_table}(term, df)
    SELECT term, COUNT(DISTINCT doc_id) FROM {idx_table} GROUP BY term
    ON CONFLICT (term) DO UPDATE SET df = EXCLUDED.df;

    UPDATE {stats_table}
    SET total_docs  = (SELECT COUNT(*) FROM {doclen_table}),
        len_sum     = (SELECT COALESCE(SUM(doc_len), 0) FROM {doclen_table}),
        avg_doc_len = (SELECT CASE WHEN COUNT(*) > 0
                              THEN SUM(doc_len)::float / COUNT(*) ELSE 1 END
                       FROM {doclen_table})
    WHERE id = 1;
END;
$$;

-- 증분 INSERT: 신규 문서 1개 추가
CREATE OR REPLACE FUNCTION {fn_add}(p_doc_id TEXT, p_text TEXT) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE rec_len INT;
BEGIN
    PERFORM {fn_delete}(p_doc_id);  -- 기존 항목 있으면 먼저 삭제

    INSERT INTO {idx_table}(term, doc_id, tf)
    SELECT lexeme, p_doc_id, array_length(positions, 1)::float
    FROM unnest(to_tsvector('{ts_config}', p_text))
    ON CONFLICT (term, doc_id) DO UPDATE SET tf = EXCLUDED.tf;

    rec_len := (SELECT COALESCE(SUM(array_length(positions, 1)), 0)
                FROM unnest(to_tsvector('{ts_config}', p_text)));

    INSERT INTO {doclen_table}(doc_id, doc_len) VALUES (p_doc_id, rec_len)
    ON CONFLICT (doc_id) DO UPDATE SET doc_len = EXCLUDED.doc_len;

    -- df 증분: 이 문서에 등장하는 term들만 +1
    INSERT INTO {df_table}(term, df)
    SELECT lexeme, 1 FROM unnest(to_tsvector('{ts_config}', p_text))
    ON CONFLICT (term) DO UPDATE SET df = {df_table}.df + 1;

    UPDATE {stats_table}
    SET total_docs  = total_docs + 1,
        len_sum     = len_sum + rec_len,
        avg_doc_len = (len_sum + rec_len)::float / (total_docs + 1)
    WHERE id = 1;
END;
$$;

-- 증분 DELETE: 문서 1개 제거
CREATE OR REPLACE FUNCTION {fn_delete}(p_doc_id TEXT) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE old_len INT;
BEGIN
    SELECT doc_len INTO old_len FROM {doclen_table} WHERE doc_id = p_doc_id;
    IF old_len IS NULL THEN RETURN; END IF;

    -- df 감소: 이 문서에 포함된 term들만 -1
    UPDATE {df_table} SET df = df - 1
    WHERE term IN (SELECT term FROM {idx_table} WHERE doc_id = p_doc_id);
    DELETE FROM {df_table} WHERE df <= 0;

    DELETE FROM {idx_table}    WHERE doc_id = p_doc_id;
    DELETE FROM {doclen_table} WHERE doc_id = p_doc_id;

    UPDATE {stats_table}
    SET total_docs  = GREATEST(total_docs - 1, 0),
        len_sum     = GREATEST(len_sum - old_len, 0),
        avg_doc_len = CASE WHEN total_docs - 1 > 0
                      THEN (len_sum - old_len)::float / (total_docs - 1)
                      ELSE 1 END
    WHERE id = 1;
END;
$$;

-- 증분 UPDATE: delete + add
CREATE OR REPLACE FUNCTION {fn_update}(p_doc_id TEXT, p_text TEXT) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM {fn_delete}(p_doc_id);
    PERFORM {fn_add}(p_doc_id, p_text);
END;
$$;

-- 검색: df/doclen JOIN → 풀스캔 제거
CREATE OR REPLACE FUNCTION {fn_search}(query TEXT, topk INT)
RETURNS TABLE(doc_id TEXT, score FLOAT)
LANGUAGE plpgsql AS $$
DECLARE
    k1      FLOAT := 1.2;
    b       FLOAT := 0.75;
    N       INT;
    avgdl   FLOAT;
    q_terms TEXT[];
BEGIN
    SELECT total_docs, avg_doc_len INTO N, avgdl FROM {stats_table} WHERE id = 1;
    IF N IS NULL OR N = 0 THEN RETURN; END IF;
    q_terms := ARRAY(SELECT lexeme FROM unnest(to_tsvector('{ts_config}', query)));
    IF array_length(q_terms, 1) IS NULL THEN RETURN; END IF;
    RETURN QUERY
    SELECT i.doc_id,
           SUM(
               ln(1 + (N - df.df + 0.5) / (df.df + 0.5)) *
               ((k1 + 1) * i.tf) /
               (k1 * (1 - b + b * (dl.doc_len::float / avgdl)) + i.tf)
           )::float AS bm25
    FROM {idx_table} i
    JOIN {df_table}     df ON df.term   = i.term
    JOIN {doclen_table} dl ON dl.doc_id = i.doc_id
    WHERE i.term = ANY(q_terms)
    GROUP BY i.doc_id
    ORDER BY bm25 DESC
    LIMIT topk;
END;
$$;
"""

def setup_plpgsql_bm25(conn, table: str,
                       ts_config: str = 'pg_catalog.simple',
                       idx_suffix: str = '') -> bool:
    name = f"{table}{idx_suffix}"
    idx     = f"bm25idx_{name}"
    df_tbl  = f"bm25df_{name}"
    doclen  = f"bm25doclen_{name}"
    stats   = f"bm25stats_{name}"
    fn_build  = f"bm25_build_{name}"
    fn_add    = f"bm25_add_{name}"
    fn_delete = f"bm25_delete_{name}"
    fn_update = f"bm25_update_{name}"
    fn_search = f"bm25_search_{name}"
    method_tag = f"2-I{idx_suffix}"
    try:
        with conn.cursor() as cur:
            for tbl in [idx, df_tbl, doclen, stats]:
                cur.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE")
            for fn, sig in [(fn_build, "TEXT"), (fn_add, "TEXT,TEXT"),
                            (fn_delete, "TEXT"), (fn_update, "TEXT,TEXT"),
                            (fn_search, "TEXT,INT")]:
                cur.execute(f"DROP FUNCTION IF EXISTS {fn}({sig}) CASCADE")
        conn.commit()
        sql = _PLPGSQL_BM25_SETUP.format(
            idx_table=idx, df_table=df_tbl, doclen_table=doclen,
            stats_table=stats, fn_build=fn_build, fn_add=fn_add,
            fn_delete=fn_delete, fn_update=fn_update, fn_search=fn_search,
            ts_config=ts_config,
        )
        with conn.cursor() as cur:
            cur.execute(sql)
            t0 = time.perf_counter()
            cur.execute(f"SELECT {fn_build}(%s)", (table,))
            cur.execute(f"SELECT COUNT(*) FROM {idx}")
            n_terms = cur.fetchone()[0]
        conn.commit()
        elapsed = time.perf_counter() - t0
        print(f"    [{method_tag}] pl/pgsql BM25 ({ts_config}) built in {elapsed:.1f}s ({n_terms} term-doc pairs)")
        return True
    except Exception as e:
        print(f"    [{method_tag}] FAILED: {e}")
        conn.rollback()
        return False


def search_plpgsql_bm25(conn, query_text: str, table: str, k: int = 10,
                        idx_suffix: str = '') -> List[str]:
    fn_search = f"bm25_search_{table}{idx_suffix}"
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT doc_id FROM {fn_search}(%s, %s)", (query_text, k))
            return [row[0] for row in cur.fetchall()]
    except Exception:
        conn.rollback()
        return []


# ---------------------------------------------------------------------------
# Evaluation loop
# ---------------------------------------------------------------------------

def evaluate_method(
    method_id: str,
    method_name: str,
    search_fn,
    conn,
    docs: List[Dict],
    queries: List[Dict],
    table: str,
    k: int = 10,
) -> Dict:
    ndcg_scores, recall_scores, mrr_scores, latencies = [], [], [], []
    zero_result_count = 0

    for q in queries:
        rel_ids = set(str(r) for r in q.get("relevant_ids", []))
        if not rel_ids:
            continue
        t0 = time.perf_counter()
        ranked = search_fn(conn, q["text"], table, k)
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
        "n_docs": len(docs),
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
    parser = argparse.ArgumentParser(description="Phase 2: tsvector Korean 비교")
    parser.add_argument("--db-url",          default="postgresql://postgres:postgres@localhost:5432/dev")
    parser.add_argument("--paradedb-url",    default="postgresql://postgres:postgres@localhost:5433/dev")
    parser.add_argument("--pgroonga-url",    default="postgresql://postgres:postgres@localhost:5435/dev")
    parser.add_argument("--miracl-docs",     default="data/miracl/docs_ko_miracl.json")
    parser.add_argument("--miracl-queries",  default="data/miracl/queries_dev.json")
    parser.add_argument("--ezis-chunks",     default="data/ezis/chunks.json")
    parser.add_argument("--ezis-queries",    default="data/ezis/queries.json")
    parser.add_argument("--output-dir",      default="results/phase2")
    parser.add_argument("--k", type=int,     default=10)
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    print("[Phase 2] Loading datasets...")
    miracl_docs    = [{"id": str(d["id"]), "text": d["text"]}
                      for d in json.load(open(args.miracl_docs))]
    miracl_queries = [{"text": q["text"],
                       "relevant_ids": [str(r) for r in q.get("relevant_ids", [])]}
                      for q in json.load(open(args.miracl_queries))]
    ezis_docs      = [{"id": str(c["id"]), "text": c["text"]}
                      for c in json.load(open(args.ezis_chunks))]
    ezis_queries   = [{"text": q["text"],
                       "relevant_ids": [str(r) for r in q.get("relevant_ids", [])]}
                      for q in json.load(open(args.ezis_queries))]

    print(f"  MIRACL: {len(miracl_docs)} docs, {len(miracl_queries)} queries")
    print(f"  EZIS:   {len(ezis_docs)} docs, {len(ezis_queries)} queries")

    conn = psycopg2.connect(args.db_url)
    all_results: Dict[str, List] = {"miracl": [], "ezis": []}

    # Try optional DB connections
    paradedb_conn: Optional[psycopg2.extensions.connection] = None
    pgroonga_conn: Optional[psycopg2.extensions.connection] = None

    try:
        paradedb_conn = psycopg2.connect(args.paradedb_url)
        print("  ParadeDB: connected (port 5433)")
    except Exception as e:
        print(f"  ParadeDB: not available — {e}")

    try:
        pgroonga_conn = psycopg2.connect(args.pgroonga_url)
        print("  pgroonga: connected (port 5435)")
    except Exception as e:
        print(f"  pgroonga: not available — {e}")

    for dataset_name, docs, queries, table in [
        ("miracl", miracl_docs, miracl_queries, "phase2_miracl"),
        ("ezis",   ezis_docs,   ezis_queries,   "phase2_ezis"),
    ]:
        print(f"\n[{dataset_name.upper()}] Setting up documents table ({len(docs)} docs)...")
        setup_documents_table(conn, docs, table)

        # ---- 2-A: textsearch_ko (MeCab + mecab-ko-dic) ----
        print(f"[{dataset_name.upper()}] Method 2-A: textsearch_ko (MeCab)...")
        ok_a = setup_textsearch_ko(conn, table)
        if ok_a:
            r = evaluate_method("2-A", "textsearch_ko (MeCab)", search_textsearch_ko,
                                conn, docs, queries, table, args.k)
            r["dataset"] = dataset_name
        else:
            r = {"method_id": "2-A", "method": "textsearch_ko (MeCab)", "dataset": dataset_name,
                 "ndcg_at_10": None, "note": "mecab-ko-dic not installed"}
        all_results[dataset_name].append(r)

        # ---- 2-B: plpython3u + kiwipiepy ----
        print(f"[{dataset_name.upper()}] Method 2-B: plpython3u + kiwipiepy...")
        ok_b = setup_plpython_tsvector(conn, table)
        if ok_b:
            r = evaluate_method("2-B", "plpython3u+kiwipiepy", search_plpython,
                                conn, docs, queries, table, args.k)
            r["dataset"] = dataset_name
            all_results[dataset_name].append(r)

        # ---- 2-C: korean_bigram (custom C parser) ----
        print(f"[{dataset_name.upper()}] Method 2-C: korean_bigram (custom C parser)...")
        ok_c = setup_korean_bigram(conn, table)
        if ok_c:
            r = evaluate_method("2-C", "korean_bigram (C parser)", search_korean_bigram,
                                conn, docs, queries, table, args.k)
            r["dataset"] = dataset_name
        else:
            r = {"method_id": "2-C", "method": "korean_bigram (C parser)", "dataset": dataset_name,
                 "ndcg_at_10": None, "note": "extension not installed"}
        all_results[dataset_name].append(r)

        # ---- 2-D: ParadeDB pg_search ----
        print(f"[{dataset_name.upper()}] Method 2-D: ParadeDB pg_search (BM25)...")
        if paradedb_conn:
            ok_d = setup_paradedb(paradedb_conn, table, docs)
            if ok_d:
                r = evaluate_method("2-D", "ParadeDB pg_search (BM25)", search_paradedb,
                                    paradedb_conn, docs, queries, table, args.k)
                r["dataset"] = dataset_name
            else:
                r = {"method_id": "2-D", "method": "ParadeDB pg_search (BM25)", "dataset": dataset_name,
                     "ndcg_at_10": None, "note": "ParadeDB setup failed"}
        else:
            r = {"method_id": "2-D", "method": "ParadeDB pg_search (BM25)", "dataset": dataset_name,
                 "ndcg_at_10": None, "note": "ParadeDB not available"}
        all_results[dataset_name].append(r)

        # ---- 2-E: pg_tokenizer from scratch (Rust/pgrx) — skipped ----
        print(f"[{dataset_name.upper()}] Method 2-E: pg_tokenizer from scratch — skipped (requires Rust/pgrx development)")
        all_results[dataset_name].append({
            "method_id": "2-E", "method": "pg_tokenizer from scratch (Rust/pgrx)",
            "dataset": dataset_name, "ndcg_at_10": None,
            "note": "SKIPPED: requires Rust/pgrx extension development (out of scope for benchmark run)"
        })

        # ---- 2-F: pgroonga ----
        print(f"[{dataset_name.upper()}] Method 2-F: pgroonga (Groonga FTS)...")
        if pgroonga_conn:
            ok_f = setup_pgroonga(pgroonga_conn, table, docs)
            if ok_f:
                r = evaluate_method("2-F", "pgroonga (Groonga FTS)", search_pgroonga,
                                    pgroonga_conn, docs, queries, table, args.k)
                r["dataset"] = dataset_name
            else:
                r = {"method_id": "2-F", "method": "pgroonga (Groonga FTS)", "dataset": dataset_name,
                     "ndcg_at_10": None, "note": "pgroonga setup failed"}
        else:
            r = {"method_id": "2-F", "method": "pgroonga (Groonga FTS)", "dataset": dataset_name,
                 "ndcg_at_10": None, "note": "pgroonga container not available"}
        all_results[dataset_name].append(r)

        # ---- 2-G: pg_bigm ----
        print(f"[{dataset_name.upper()}] Method 2-G: pg_bigm (bigram)...")
        ok_g = setup_pg_bigm(conn, table)
        if ok_g:
            r = evaluate_method("2-G", "pg_bigm (bigram)", search_pg_bigm,
                                conn, docs, queries, table, args.k)
            r["dataset"] = dataset_name
        else:
            r = {"method_id": "2-G", "method": "pg_bigm (bigram)", "dataset": dataset_name,
                 "ndcg_at_10": None, "note": "pg_bigm not installed"}
        all_results[dataset_name].append(r)

        # ---- 2-H-a: pg_textsearch + public.korean (MeCab BM25) — 핵심 실험 ----
        print(f"[{dataset_name.upper()}] Method 2-H-a: pg_textsearch + public.korean (MeCab BM25)...")
        ok_ha = setup_pg_textsearch(conn, table, ts_config="public.korean", idx_suffix="korean")
        if ok_ha:
            r = evaluate_method("2-H-a", "pg_textsearch + MeCab (BM25/WAND)", search_pg_textsearch,
                                conn, docs, queries, table, args.k)
            r["dataset"] = dataset_name
        else:
            r = {"method_id": "2-H-a", "method": "pg_textsearch + MeCab (BM25/WAND)",
                 "dataset": dataset_name, "ndcg_at_10": None,
                 "note": "pg_textsearch not installed or public.korean config not found"}
        all_results[dataset_name].append(r)

        # ---- 2-H-b: pg_textsearch + public.korean_bigram (C parser BM25) ----
        print(f"[{dataset_name.upper()}] Method 2-H-b: pg_textsearch + public.korean_bigram (BM25)...")
        ok_hb = setup_pg_textsearch(conn, table, ts_config="public.korean_bigram", idx_suffix="kbg")
        if ok_hb:
            r = evaluate_method("2-H-b", "pg_textsearch + korean_bigram (BM25/WAND)", search_pg_textsearch,
                                conn, docs, queries, table, args.k)
            r["dataset"] = dataset_name
        else:
            r = {"method_id": "2-H-b", "method": "pg_textsearch + korean_bigram (BM25/WAND)",
                 "dataset": dataset_name, "ndcg_at_10": None,
                 "note": "pg_textsearch not installed or public.korean_bigram config not found"}
        all_results[dataset_name].append(r)

        # ---- 2-I: pl/pgsql custom BM25 (pg_catalog.simple tokenizer) ----
        print(f"[{dataset_name.upper()}] Method 2-I: pl/pgsql custom BM25 (simple)...")
        ok_i = setup_plpgsql_bm25(conn, table, ts_config='pg_catalog.simple', idx_suffix='')
        if ok_i:
            r = evaluate_method("2-I", "pl/pgsql custom BM25",
                                lambda c, q, t, k: search_plpgsql_bm25(c, q, t, k, idx_suffix=''),
                                conn, docs, queries, table, args.k)
            r["dataset"] = dataset_name
        else:
            r = {"method_id": "2-I", "method": "pl/pgsql custom BM25", "dataset": dataset_name,
                 "ndcg_at_10": None, "note": "setup failed"}
        all_results[dataset_name].append(r)

        # ---- 2-I-korean: pl/pgsql custom BM25 + MeCab (public.korean tsvector) ----
        print(f"[{dataset_name.upper()}] Method 2-I-korean: pl/pgsql BM25 + MeCab (public.korean)...")
        ok_ik = setup_plpgsql_bm25(conn, table, ts_config='public.korean', idx_suffix='_korean')
        if ok_ik:
            r = evaluate_method("2-I-korean", "pl/pgsql BM25 + MeCab (public.korean)",
                                lambda c, q, t, k: search_plpgsql_bm25(c, q, t, k, idx_suffix='_korean'),
                                conn, docs, queries, table, args.k)
            r["dataset"] = dataset_name
        else:
            r = {"method_id": "2-I-korean", "method": "pl/pgsql BM25 + MeCab (public.korean)",
                 "dataset": dataset_name, "ndcg_at_10": None, "note": "setup failed (public.korean not available?)"}
        all_results[dataset_name].append(r)

    conn.close()
    if paradedb_conn:
        paradedb_conn.close()
    if pgroonga_conn:
        pgroonga_conn.close()

    # Save results
    json_path = os.path.join(args.output_dir, "phase2_tsvector_comparison.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\n[Phase 2] Saved: {json_path}")

    # Summary
    print("\n" + "=" * 60)
    for dataset_name in ["miracl", "ezis"]:
        print(f"\n{dataset_name.upper()} results:")
        valid = [x for x in all_results[dataset_name] if x.get("ndcg_at_10") is not None]
        for r in sorted(valid, key=lambda x: -x.get("ndcg_at_10", 0)):
            print(f"  {r['method_id']} {r['method']:35} NDCG@10={r['ndcg_at_10']:.4f}")
        skipped = [x for x in all_results[dataset_name] if x.get("ndcg_at_10") is None]
        for r in skipped:
            print(f"  {r['method_id']} {r['method']:35} {r.get('note', 'skipped')}")


if __name__ == "__main__":
    main()
