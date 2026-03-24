"""Phase 2: Compare BM25 implementations (pl/pgsql, pgvector sparse, pg_bm25)."""
import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Dict

try:
    import psycopg2
except ImportError:
    psycopg2 = None  # type: ignore[assignment]

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from experiments.common.bm25_module import BM25Embedder_PG, bm25_sparse_search, setup_sparse_bm25_table
from benchmark.runner import run_benchmark
from benchmark.eval import compute_ndcg, compute_recall, compute_mrr


def run_plpgsql_bm25(
    conn,
    queries: List[Dict],
    tokenizer_name: str = "mecab",
    k: int = 10,
) -> List[Dict]:
    """
    Use BM25Embedder Python path (inverted_index + bm25_ranking SQL). MeCab held constant.
    Returns [{query_id, ranked_ids: List[str]}].
    """
    results = []
    with conn.cursor() as cur:
        for q in queries:
            query_id = q.get("query_id", q.get("text", ""))
            query_text = q["text"] if isinstance(q, dict) else q
            cur.execute(
                """
                SELECT e.id::text
                FROM bm25_ranking(%s) AS b
                JOIN text_embedding e ON e.id = b.doc_id
                ORDER BY b.score DESC
                LIMIT %s;
                """,
                (query_text, k),
            )
            rows = cur.fetchall()
            results.append({"query_id": query_id, "ranked_ids": [row[0] for row in rows]})
    return results


def run_pgvector_sparse_bm25(
    conn,
    embedder,
    queries: List[Dict],
    k: int = 10,
) -> List[Dict]:
    """
    Use bm25_sparse_search() with pgvector cosine distance on BM25 sparse vectors.
    embedder: a fitted BM25Embedder_PG instance.
    Returns [{query_id, ranked_ids: List[str]}].
    """
    results = []
    for q in queries:
        query_id = q.get("query_id", q.get("text", ""))
        query_text = q["text"] if isinstance(q, dict) else q
        rows = bm25_sparse_search(embedder, query_text, k=k)
        doc_ids = [str(r[0]) for r in rows]
        results.append({"query_id": query_id, "ranked_ids": doc_ids})
    return results


def run_pg_bm25_paradedb(conn, queries: List[Dict], k: int = 10) -> List[Dict]:
    """
    pg_bm25 (ParadeDB) integration.

    NOT IMPLEMENTED: Requires ParadeDB Docker image (paradedb/paradedb:latest).
    To integrate:
      1. Update docker-compose.yml to use paradedb/paradedb:latest
      2. CREATE INDEX ON documents USING bm25(doc_id, text) WITH (key_field='doc_id');
      3. Query: SELECT doc_id FROM documents.search('query text', limit=k);
    """
    raise NotImplementedError(
        "pg_bm25 requires ParadeDB image. "
        "Update docker-compose.yml to use paradedb/paradedb:latest and retry."
    )


def run_explain_analyze(
    conn, query_text: str, method: str, tokenizer_name: str = "mecab"
) -> str:
    """Return EXPLAIN ANALYZE output as a string for the given method and query."""
    with conn.cursor() as cur:
        if method == "plpgsql":
            cur.execute(
                """
                EXPLAIN ANALYZE
                SELECT e.id::text
                FROM bm25_ranking(%s) AS b
                JOIN text_embedding e ON e.id = b.doc_id
                ORDER BY b.score DESC
                LIMIT 10;
                """,
                (query_text,),
            )
        elif method == "pgvector-sparse":
            cur.execute(
                """
                EXPLAIN ANALYZE
                SELECT id::text, text, emb_sparse <=> emb_sparse AS bm25_score
                FROM text_embedding_sparse_bm25
                ORDER BY bm25_score
                LIMIT 10;
                """
            )
        else:
            raise ValueError(f"Unknown method for EXPLAIN ANALYZE: {method!r}")
        rows = cur.fetchall()
        return "\n".join(row[0] for row in rows)


def main():
    parser = argparse.ArgumentParser(description="Phase 2: BM25 implementation comparison")
    parser.add_argument("--db-url", required=True)
    parser.add_argument(
        "--method", choices=["plpgsql", "pgvector-sparse", "pg-bm25"], required=True
    )
    parser.add_argument("--dataset-size", type=int, default=10000)
    parser.add_argument("--queries-file", required=True)
    parser.add_argument("--output-dir", default="results/phase2")
    parser.add_argument(
        "--tokenizer",
        default="mecab",
        choices=["mecab", "kiwi-cong", "kiwi-knlm", "okt", "kkma", "whitespace"],
    )
    args = parser.parse_args()

    conn = psycopg2.connect(args.db_url)

    with open(args.queries_file, encoding="utf-8") as f:
        queries = json.load(f)

    os.makedirs(args.output_dir, exist_ok=True)

    if args.method == "plpgsql":
        def search_fn(query_text: str) -> List[str]:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT e.id::text
                    FROM bm25_ranking(%s) AS b
                    JOIN text_embedding e ON e.id = b.doc_id
                    ORDER BY b.score DESC LIMIT 10;
                    """,
                    (query_text,),
                )
                return [row[0] for row in cur.fetchall()]

        method_name = f"plpgsql_bm25_{args.tokenizer}"

    elif args.method == "pgvector-sparse":
        tokenizer_map = {"mecab": "Mecab", "kiwi-cong": "kiwi-cong", "kiwi-knlm": "kiwi-knlm", "okt": "Okt", "kkma": "kkma", "whitespace": "whitespace"}
        tok = tokenizer_map[args.tokenizer]
        with conn.cursor() as cur:
            cur.execute("SELECT text FROM text_embedding ORDER BY id;")
            corpus = [row[0] for row in cur.fetchall()]
        table_name = f"text_embedding_sparse_bm25_{args.tokenizer.replace('-', '_')}"
        embedder = BM25Embedder_PG(tokenizer=tok)
        embedder.fit(corpus)
        setup_sparse_bm25_table(embedder, corpus, table=table_name)

        def search_fn(query_text: str) -> List[str]:
            rows = bm25_sparse_search(embedder, query_text, k=10, table=table_name)
            return [str(r[0]) for r in rows]

        method_name = f"pgvector_sparse_bm25_{args.tokenizer}"

    elif args.method == "pg-bm25":
        run_pg_bm25_paradedb(conn, queries)
        conn.close()
        return

    output_path = os.path.join(
        args.output_dir,
        f"phase2_{args.method.replace('-', '_')}_{args.tokenizer}_{args.dataset_size}.json",
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
