"""
Phase 7 Hybrid Setup: BGE-M3 임베딩 + 인덱스 구성

1. p7_hybrid_miracl: text_embedding 10K docs → 1024-dim BGE-M3 임베딩
2. p7_hybrid_ezis:   data/ezis/chunks.json 97 docs → 1024-dim BGE-M3 임베딩
3. p7_query_emb:     MIRACL + EZIS 쿼리 임베딩 저장
4. 각 테이블에 BM25 + GIN + HNSW 인덱스 생성

Usage:
  uv run python3 experiments/phase7_scaling/phase7_hybrid_setup.py \\
    --db-url postgresql://postgres:postgres@localhost:5432/dev
"""

import argparse
import json
import time
from typing import List

import psycopg2
import psycopg2.extras

MIRACL_QUERIES_PATH = "data/miracl/queries_dev.json"
EZIS_QUERIES_PATH   = "data/ezis/queries.json"
EZIS_CHUNKS_PATH    = "data/ezis/chunks.json"
BATCH = 64


def connect(db_url: str):
    conn = psycopg2.connect(db_url)
    conn.autocommit = False
    return conn


def load_model():
    from FlagEmbedding import BGEM3FlagModel
    import torch
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"  Loading BGE-M3 on {device}...", end="", flush=True)
    t0 = time.perf_counter()
    model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True, device=device)
    print(f" {time.perf_counter()-t0:.1f}s")
    return model


def embed_texts(model, texts: List[str], batch_size: int = BATCH) -> List[List[float]]:
    results = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i+batch_size]
        out = model.encode(batch, batch_size=batch_size,
                           max_length=512, return_dense=True)
        results.extend(out["dense_vecs"].tolist())
        print(f"    {i+len(batch)}/{len(texts)}", end="\r")
    print()
    return results


# ---------------------------------------------------------------------------
# Setup p7_hybrid_miracl
# ---------------------------------------------------------------------------

def setup_miracl(conn, model):
    print("\n--- p7_hybrid_miracl (10K docs) ---")

    # Load docs from text_embedding
    with conn.cursor() as cur:
        cur.execute("SELECT id, text FROM text_embedding ORDER BY id")
        rows = cur.fetchall()
    docs = [{"id": str(r[0]), "text": r[1]} for r in rows]
    print(f"  Loaded {len(docs)} docs from text_embedding")

    # Create table
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS p7_hybrid_miracl CASCADE")
        cur.execute("""
            CREATE TABLE p7_hybrid_miracl (
                id        TEXT PRIMARY KEY,
                text      TEXT,
                tsv       tsvector,
                dense_vec vector(1024)
            )
        """)
    conn.commit()

    # Embed docs
    print(f"  Embedding {len(docs)} docs...")
    t0 = time.perf_counter()
    vecs = embed_texts(model, [d["text"] for d in docs])
    print(f"  Embed: {time.perf_counter()-t0:.1f}s")

    # Insert
    t0 = time.perf_counter()
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(
            cur,
            """INSERT INTO p7_hybrid_miracl (id, text, dense_vec)
               VALUES (%s, %s, %s)""",
            [(d["id"], d["text"], v) for d, v in zip(docs, vecs)],
            page_size=200)
        cur.execute(
            "UPDATE p7_hybrid_miracl SET tsv = to_tsvector('public.korean', text)")
    conn.commit()
    print(f"  Insert+tsv: {time.perf_counter()-t0:.1f}s")

    # Indexes
    print("  Building indexes...")
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS pg_textsearch")
        cur.execute("""
            CREATE INDEX idx_p7_miracl_bm25 ON p7_hybrid_miracl
            USING bm25(text) WITH (text_config='public.korean')
        """)
        cur.execute(
            "CREATE INDEX idx_p7_miracl_gin ON p7_hybrid_miracl USING GIN(tsv)")
        cur.execute("""
            CREATE INDEX idx_p7_miracl_hnsw ON p7_hybrid_miracl
            USING hnsw(dense_vec vector_cosine_ops)
            WITH (m=16, ef_construction=200)
        """)
    conn.commit()
    print("  [OK] bm25 + gin + hnsw")


# ---------------------------------------------------------------------------
# Setup p7_hybrid_ezis
# ---------------------------------------------------------------------------

def setup_ezis(conn, model):
    print("\n--- p7_hybrid_ezis (97 docs) ---")

    with open(EZIS_CHUNKS_PATH, encoding="utf-8") as f:
        chunks = json.load(f)
    docs = [{"id": c["id"], "text": c["text"]} for c in chunks]
    print(f"  Loaded {len(docs)} docs from {EZIS_CHUNKS_PATH}")

    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS p7_hybrid_ezis CASCADE")
        cur.execute("""
            CREATE TABLE p7_hybrid_ezis (
                id        TEXT PRIMARY KEY,
                text      TEXT,
                tsv       tsvector,
                dense_vec vector(1024)
            )
        """)
    conn.commit()

    print(f"  Embedding {len(docs)} docs...")
    vecs = embed_texts(model, [d["text"] for d in docs])

    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(
            cur,
            """INSERT INTO p7_hybrid_ezis (id, text, dense_vec)
               VALUES (%s, %s, %s)""",
            [(d["id"], d["text"], v) for d, v in zip(docs, vecs)],
            page_size=200)
        cur.execute(
            "UPDATE p7_hybrid_ezis SET tsv = to_tsvector('public.korean', text)")
    conn.commit()

    with conn.cursor() as cur:
        cur.execute("""
            CREATE INDEX idx_p7_ezis_bm25 ON p7_hybrid_ezis
            USING bm25(text) WITH (text_config='public.korean')
        """)
        cur.execute(
            "CREATE INDEX idx_p7_ezis_gin ON p7_hybrid_ezis USING GIN(tsv)")
        cur.execute("""
            CREATE INDEX idx_p7_ezis_hnsw ON p7_hybrid_ezis
            USING hnsw(dense_vec vector_cosine_ops)
            WITH (m=16, ef_construction=200)
        """)
    conn.commit()
    print("  [OK] bm25 + gin + hnsw")


# ---------------------------------------------------------------------------
# Pre-compute query embeddings
# ---------------------------------------------------------------------------

def setup_query_emb(conn, model):
    print("\n--- p7_query_emb ---")

    with open(MIRACL_QUERIES_PATH, encoding="utf-8") as f:
        miracl_q = json.load(f)
    with open(EZIS_QUERIES_PATH, encoding="utf-8") as f:
        ezis_q = json.load(f)

    queries = (
        [{"id": q["query_id"], "text": q["text"], "dataset": "miracl"} for q in miracl_q] +
        [{"id": q["query_id"], "text": q["text"], "dataset": "ezis"}   for q in ezis_q]
    )
    print(f"  Queries: {len(queries)} (miracl={len(miracl_q)} ezis={len(ezis_q)})")

    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS p7_query_emb")
        cur.execute("""
            CREATE TABLE p7_query_emb (
                query_id TEXT PRIMARY KEY,
                text     TEXT,
                dataset  TEXT,
                emb      vector(1024)
            )
        """)
    conn.commit()

    print(f"  Embedding {len(queries)} queries...")
    vecs = embed_texts(model, [q["text"] for q in queries])

    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(
            cur,
            "INSERT INTO p7_query_emb (query_id, text, dataset, emb) VALUES (%s,%s,%s,%s)",
            [(q["id"], q["text"], q["dataset"], v) for q, v in zip(queries, vecs)],
            page_size=200)
    conn.commit()
    print(f"  [OK] {len(queries)} query embeddings stored")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-url",
                        default="postgresql://postgres:postgres@localhost:5432/dev")
    parser.add_argument("--skip-miracl", action="store_true")
    parser.add_argument("--skip-ezis",   action="store_true")
    parser.add_argument("--skip-queries",action="store_true")
    args = parser.parse_args()

    print("=" * 60)
    print("Phase 7 Hybrid Setup")
    print("=" * 60)

    conn = psycopg2.connect(args.db_url)
    conn.autocommit = False

    model = load_model()

    if not args.skip_miracl:
        setup_miracl(conn, model)
    if not args.skip_ezis:
        setup_ezis(conn, model)
    if not args.skip_queries:
        setup_query_emb(conn, model)

    conn.close()
    print("\nSetup complete.")


if __name__ == "__main__":
    main()
