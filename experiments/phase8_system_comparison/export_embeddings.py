"""
Phase 8: Query Embedding Export

Exports pre-computed BGE-M3 query embeddings from PostgreSQL (p7_query_emb table)
to JSON files for use with external search systems (ES, Qdrant, Vespa).

Usage:
  uv run python3 experiments/phase8_system_comparison/export_embeddings.py \
    --db-url postgresql://postgres:postgres@localhost:5432/dev \
    --output-dir data/phase8
"""

import argparse
import json
import os

import psycopg2

DATASETS = ["miracl", "ezis"]


def export_embeddings(conn, dataset: str, output_dir: str) -> str:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT query_id, emb FROM p7_query_emb WHERE dataset = %s",
            (dataset,)
        )
        rows = cur.fetchall()

    embs = {r[0]: r[1] for r in rows}
    path = os.path.join(output_dir, f"query_embs_{dataset}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(embs, f, ensure_ascii=False)
    print(f"  {dataset}: {len(embs)} embeddings → {path}")
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-url",
                        default="postgresql://postgres:postgres@localhost:5432/dev")
    parser.add_argument("--output-dir", default="data/phase8")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    conn = psycopg2.connect(args.db_url)
    conn.autocommit = True
    print("Connected to PostgreSQL")

    for dataset in DATASETS:
        export_embeddings(conn, dataset, args.output_dir)

    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
