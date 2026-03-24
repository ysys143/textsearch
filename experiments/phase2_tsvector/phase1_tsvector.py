"""Phase 1: Compare textsearch_ko tsvector vs Python-side tokenization + BM25."""
import argparse
import json
import os
import time
import sys
from pathlib import Path
from typing import List, Dict, Optional

try:
    import psycopg2
except ImportError:
    psycopg2 = None  # type: ignore[assignment]

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from experiments.common.bm25_module import BM25Embedder_PG, bm25_sparse_search
from benchmark.runner import run_benchmark
from benchmark.eval import compute_ndcg, compute_recall, compute_mrr


def _detect_ts_config(conn) -> str:
    """Return 'public.korean' if available, else fall back to 'simple'."""
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_ts_config WHERE cfgname = 'korean'")
        return "public.korean" if cur.fetchone() else "simple"


def setup_textsearch_ko_index(conn, table: str = "documents") -> None:
    """Add tsvector column + GIN index using 'public.korean' config (falls back to 'simple')."""
    ts_config = _detect_ts_config(conn)
    print(f"[phase1] Using text search config: {ts_config}")
    with conn.cursor() as cur:
        cur.execute(f"""
            ALTER TABLE {table}
            ADD COLUMN IF NOT EXISTS tsv tsvector;
        """)
        cur.execute(f"""
            UPDATE {table} SET tsv = to_tsvector('{ts_config}', text)
            WHERE tsv IS NULL;
        """)
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_tsv ON {table} USING GIN(tsv);
        """)
    conn.commit()


def run_textsearch_ko(conn, queries: List[Dict], k: int = 10) -> List[Dict]:
    """
    Run ts_rank queries using 'public.korean' tsvector config (falls back to 'simple').
    Returns [{query_id, ranked_ids: List[str]}].
    """
    ts_config = _detect_ts_config(conn)
    results = []
    with conn.cursor() as cur:
        for q in queries:
            query_id = q.get("query_id", q.get("text", ""))
            query_text = q["text"] if isinstance(q, dict) else q
            cur.execute(
                f"""
                SELECT id::text
                FROM documents
                WHERE tsv @@ plainto_tsquery('{ts_config}', %s)
                ORDER BY ts_rank(tsv, plainto_tsquery('{ts_config}', %s)) DESC
                LIMIT %s;
                """,
                (query_text, query_text, k),
            )
            rows = cur.fetchall()
            results.append({"query_id": query_id, "ranked_ids": [row[0] for row in rows]})
    return results


def setup_python_bm25_index(conn, tokenizer_name: str, table: str = "documents") -> None:
    """Build BM25Embedder inverted index using specified tokenizer."""
    tokenizer_map = {"mecab": "Mecab", "kiwi-cong": "kiwi-cong", "kiwi-knlm": "kiwi-knlm", "okt": "Okt"}
    tok = tokenizer_map.get(tokenizer_name, tokenizer_name)

    with conn.cursor() as cur:
        cur.execute(f"SELECT id, text FROM {table} ORDER BY id;")
        rows = cur.fetchall()

    corpus = [row[1] for row in rows]
    embedder = BM25Embedder_PG(tokenizer=tok)
    embedder.fit(corpus)

    # Store fitted embedder state as inverted index in DB
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bm25_inverted_index (
                term TEXT,
                doc_id TEXT,
                tf_score FLOAT,
                idf FLOAT,
                PRIMARY KEY (term, doc_id)
            );
        """)
        cur.execute("DELETE FROM bm25_inverted_index;")
        for i, (row_id, doc_text) in enumerate(rows):
            sparse = embedder.embed_document(doc_text)
            for token_id, score in sparse.items():
                token = embedder.index_to_token.get(token_id, "")
                idf = embedder.idf_dict.get(token, 0.0)
                if token:
                    cur.execute(
                        "INSERT INTO bm25_inverted_index (term, doc_id, tf_score, idf) VALUES (%s, %s, %s, %s) "
                        "ON CONFLICT (term, doc_id) DO UPDATE SET tf_score=EXCLUDED.tf_score, idf=EXCLUDED.idf;",
                        (token, str(row_id), score, idf),
                    )
    conn.commit()


def run_python_bm25(conn, embedder, queries: List[Dict], k: int = 10) -> List[Dict]:
    """
    Run BM25Embedder search with a pre-built embedder.
    embedder: a fitted BM25Embedder_PG instance.
    Returns [{query_id, ranked_ids: List[str]}].
    """
    results = []
    for q in queries:
        query_id = q.get("query_id", q.get("text", ""))
        query_text = q["text"] if isinstance(q, dict) else q
        search_rows = bm25_sparse_search(embedder, query_text, k=k)
        doc_ids = [str(r[0]) for r in search_rows]
        results.append({"query_id": query_id, "ranked_ids": doc_ids})
    return results


def measure_index_build_time(
    conn, method: str, tokenizer_name: Optional[str] = None
) -> float:
    """Return index build time in seconds."""
    start = time.perf_counter()
    if method == "textsearch-ko":
        setup_textsearch_ko_index(conn)
    elif method == "python-bm25":
        tok = tokenizer_name or "mecab"
        setup_python_bm25_index(conn, tok)
    else:
        raise ValueError(f"Unknown method: {method!r}")
    return time.perf_counter() - start


def main():
    parser = argparse.ArgumentParser(description="Phase 1: tsvector vs Python BM25")
    parser.add_argument(
        "--db-url",
        default="postgresql://postgres:postgres@localhost:5432/benchmark",
    )
    parser.add_argument(
        "--method", choices=["textsearch-ko", "python-bm25"], required=True
    )
    parser.add_argument(
        "--tokenizer",
        default="mecab",
        choices=["mecab", "kiwi-cong", "kiwi-knlm", "okt", "whitespace"],
    )
    parser.add_argument(
        "--queries-file", required=True, help="Path to queries JSON [{query_id,text,relevant_ids}]"
    )
    parser.add_argument("--output-dir", default="results/phase1")
    parser.add_argument("--dataset-size", type=int, default=10000)
    args = parser.parse_args()

    conn = psycopg2.connect(args.db_url)

    with open(args.queries_file, encoding="utf-8") as f:
        queries = json.load(f)

    os.makedirs(args.output_dir, exist_ok=True)

    if args.method == "textsearch-ko":
        setup_textsearch_ko_index(conn)
        ts_config = _detect_ts_config(conn)
        print(f"[phase1] Using text search config: {ts_config}")
        def search_fn(query_text: str) -> List[str]:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id::text FROM documents
                    WHERE tsv @@ plainto_tsquery('{ts_config}', %s)
                    ORDER BY ts_rank(tsv, plainto_tsquery('{ts_config}', %s)) DESC
                    LIMIT 10;
                    """,
                    (query_text, query_text),
                )
                return [row[0] for row in cur.fetchall()]

        method_name = f"textsearch_{ts_config.replace('.', '_').replace('public_', '')}"
    else:
        tokenizer_map = {"mecab": "Mecab", "kiwi-cong": "kiwi-cong", "kiwi-knlm": "kiwi-knlm", "okt": "Okt", "whitespace": "whitespace"}
        tok = tokenizer_map[args.tokenizer]
        with conn.cursor() as cur:
            cur.execute("SELECT text FROM documents ORDER BY id;")
            corpus = [row[0] for row in cur.fetchall()]
        embedder = BM25Embedder_PG(tokenizer=tok)
        embedder.fit(corpus)

        def search_fn(query_text: str) -> List[str]:
            rows = bm25_sparse_search(embedder, query_text, k=10)
            return [str(r[0]) for r in rows]

        method_name = f"python_bm25_{args.tokenizer}"

    output_path = os.path.join(
        args.output_dir, f"phase1_{args.method.replace('-', '_')}_{args.dataset_size}.json"
    )
    result = run_benchmark(
        search_fn=search_fn,
        queries=queries,
        method_name=method_name,
        dataset_size=args.dataset_size,
        output_path=output_path,
    )
    print(
        f"[{args.method}] ndcg@10={result['ndcg_at_10']:.4f}"
        f"  recall@10={result['recall_at_10']:.4f}"
        f"  mrr={result['mrr']:.4f}"
    )
    conn.close()


if __name__ == "__main__":
    main()
