"""
Phase 6-1: VectorChord-BM25 + textsearch_ko Full Evaluation

- 10K MIRACL Korean corpus (from text_embedding on main DB)
- 213 dev queries (all relevant docs covered)
- Compare: VectorChord-BM25 vs Phase 3 MeCab BM25 baseline

Usage:
  python3 experiments/phase6_vectorchord/phase6_1_full_eval.py \\
    --db-url postgresql://postgres:postgres@localhost:5436/dev \\
    --main-db-url postgresql://postgres:postgres@localhost:5432/dev \\
    --output-dir results/phase6
"""

import argparse
import json
import math
import os
import sys
import time
from collections import Counter
from datetime import datetime
from typing import Dict, List, Optional, Tuple

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("[ERROR] psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(1)

QUERIES_PATH = "data/miracl/queries_dev.json"
EZIS_DOCS_PATH = "data/ezis/chunks.json"
EZIS_QUERIES_PATH = "data/ezis/queries.json"

# Phase 3 MeCab baseline for comparison (from results/phase3/)
PHASE3_MECAB_NDCG = 0.4732  # Phase 3 MeCab on pgvector-sparse (to be confirmed)
PHASE5_BM25_P50_MS = 0.73   # Phase 5 BM25 v2 p50 latency


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

def load_corpus_from_db(conn_main) -> List[dict]:
    """Load all 10K docs from text_embedding table on main DB."""
    with conn_main.cursor() as cur:
        cur.execute("SELECT id, text FROM text_embedding ORDER BY id")
        rows = cur.fetchall()
    return [{"id": str(r[0]), "text": r[1]} for r in rows]


def load_queries() -> List[dict]:
    with open(QUERIES_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_ezis() -> Tuple[List[dict], List[dict]]:
    with open(EZIS_DOCS_PATH, encoding="utf-8") as f:
        docs = json.load(f)
    with open(EZIS_QUERIES_PATH, encoding="utf-8") as f:
        queries = json.load(f)
    # Normalize id to str
    for d in docs:
        d["id"] = str(d["id"])
    return docs, queries


# ---------------------------------------------------------------------------
# Vocab + bm25vector
# ---------------------------------------------------------------------------

def build_vocab(conn_main, texts: List[str], batch_size: int = 500) -> Dict[str, int]:
    """Build term->id vocab using textsearch_ko on main DB."""
    term_counts: Counter = Counter()
    total = len(texts)
    for i in range(0, total, batch_size):
        batch = texts[i:i + batch_size]
        with conn_main.cursor() as cur:
            for text in batch:
                if not text or not text.strip():
                    continue
                try:
                    cur.execute(
                        "SELECT tsvector_to_array(to_tsvector('public.korean', %s))",
                        (text,),
                    )
                    row = cur.fetchone()
                    terms = row[0] if row and row[0] else []
                    term_counts.update(terms)
                except Exception as e:
                    conn_main.rollback()
        if (i // batch_size) % 10 == 0:
            print(f"    vocab progress: {min(i + batch_size, total)}/{total}", end="\r")
    print()
    return {term: idx + 1 for idx, (term, _) in enumerate(term_counts.most_common())}


def text_to_bm25vector(conn_main, text: str, vocab: Dict[str, int]) -> Optional[str]:
    if not text or not text.strip():
        return None
    with conn_main.cursor() as cur:
        cur.execute(
            "SELECT tsvector_to_array(to_tsvector('public.korean', %s))",
            (text,),
        )
        row = cur.fetchone()
        terms = row[0] if row and row[0] else []
    counts = Counter(t for t in terms if t in vocab)
    if not counts:
        return None
    vec_str = ",".join(
        f"{vocab[t]}:{c}"
        for t, c in sorted(counts.items(), key=lambda x: vocab[x[0]])
    )
    return f"{{{vec_str}}}"


# ---------------------------------------------------------------------------
# Setup phase6 DB
# ---------------------------------------------------------------------------

def setup_table(conn_phase6, table: str, create_extension: bool = False):
    """Create (or recreate) a bm25vector corpus table on phase6 DB."""
    with conn_phase6.cursor() as cur:
        if create_extension:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vchord_bm25 CASCADE;")
        cur.execute(f"DROP TABLE IF EXISTS {table};")
        cur.execute(f"CREATE TABLE {table} (id TEXT PRIMARY KEY, emb bm25vector);")
    conn_phase6.commit()
    print(f"  [OK] Table {table} created")


def insert_corpus(
    conn_phase6, conn_main, docs: List[dict], vocab: Dict[str, int], table: str
) -> int:
    """Tokenize docs via main DB and insert bm25vectors into phase6 DB."""
    inserted = 0
    skipped = 0
    batch = []
    BATCH_SIZE = 200

    for doc in docs:
        vec = text_to_bm25vector(conn_main, doc["text"], vocab)
        if vec is None:
            skipped += 1
            continue
        batch.append((doc["id"], vec))

        if len(batch) >= BATCH_SIZE:
            with conn_phase6.cursor() as cur:
                for did, v in batch:
                    cur.execute(
                        f"INSERT INTO {table} (id, emb) VALUES (%s, %s::bm25vector)"
                        " ON CONFLICT (id) DO NOTHING",
                        (did, v),
                    )
            conn_phase6.commit()
            inserted += len(batch)
            batch = []
            print(f"    inserted {inserted}/{len(docs)}...", end="\r")

    if batch:
        with conn_phase6.cursor() as cur:
            for did, v in batch:
                cur.execute(
                    f"INSERT INTO {table} (id, emb) VALUES (%s, %s::bm25vector)"
                    " ON CONFLICT (id) DO NOTHING",
                    (did, v),
                )
        conn_phase6.commit()
        inserted += len(batch)

    print(f"    inserted {inserted}, skipped {skipped}        ")
    return inserted


def create_index(conn_phase6, table: str):
    idx = f"{table}_emb_idx"
    print(f"  Creating bm25 index ({idx})...")
    with conn_phase6.cursor() as cur:
        cur.execute(f"DROP INDEX IF EXISTS {idx};")
        cur.execute(f"CREATE INDEX {idx} ON {table} USING bm25 (emb bm25_ops);")
    conn_phase6.commit()
    print("  Index created.")


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def run_evaluation(
    conn_phase6, conn_main, queries: List[dict], vocab: Dict[str, int], table: str
) -> dict:
    """Run NDCG@10, Recall@10, MRR, latency on all valid queries."""
    idx = f"{table}_emb_idx"
    ndcg_scores, recall_scores, mrr_scores, latencies_ms = [], [], [], []
    skipped_no_vec, skipped_no_relevant = 0, 0

    for q in queries:
        q_vec = text_to_bm25vector(conn_main, q["text"], vocab)
        if q_vec is None:
            skipped_no_vec += 1
            continue

        t0 = time.perf_counter()
        with conn_phase6.cursor() as cur:
            cur.execute(
                f"SELECT id FROM {table}"
                f" ORDER BY emb <&> to_bm25query('{idx}', %s::bm25vector)"
                " LIMIT 10",
                (q_vec,),
            )
            rows = cur.fetchall()
        elapsed_ms = (time.perf_counter() - t0) * 1000

        retrieved = [r[0] for r in rows]
        if not retrieved:
            skipped_no_relevant += 1
            continue

        ndcg_scores.append(ndcg_at_k(q["relevant_ids"], retrieved))
        recall_scores.append(recall_at_k(q["relevant_ids"], retrieved))
        mrr_scores.append(mrr(q["relevant_ids"], retrieved))
        latencies_ms.append(elapsed_ms)

    if not ndcg_scores:
        return {"error": "No queries produced results"}

    latencies_ms.sort()
    n = len(latencies_ms)
    return {
        "ndcg_at_10": sum(ndcg_scores) / n,
        "recall_at_10": sum(recall_scores) / n,
        "mrr": sum(mrr_scores) / n,
        "latency_p50_ms": latencies_ms[n // 2],
        "latency_p95_ms": latencies_ms[int(n * 0.95)],
        "queries_evaluated": n,
        "queries_skipped_no_vec": skipped_no_vec,
        "queries_skipped_no_results": skipped_no_relevant,
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def write_report(
    output_dir: str,
    miracl_corpus_size: int,
    vocab_size: int,
    miracl_metrics: dict,
    ezis_metrics: dict,
) -> str:
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "phase6_1_full_eval_report.md")

    m_ndcg = miracl_metrics.get("ndcg_at_10", 0.0)
    m_p50 = miracl_metrics.get("latency_p50_ms", 0.0)
    e_ndcg = ezis_metrics.get("ndcg_at_10", 0.0) if "error" not in ezis_metrics else None
    e_p50 = ezis_metrics.get("latency_p50_ms", 0.0) if "error" not in ezis_metrics else None

    lines = [
        "# Phase 6-1: VectorChord-BM25 + textsearch_ko Full Evaluation",
        "",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Vocab size:** {vocab_size} terms",
        "",
        "---",
        "",
        "## MIRACL-ko Results (10K corpus, 213 queries)",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| NDCG@10 | **{m_ndcg:.4f}** |",
        f"| Recall@10 | {miracl_metrics.get('recall_at_10', 0):.4f} |",
        f"| MRR | {miracl_metrics.get('mrr', 0):.4f} |",
        f"| Latency p50 | {m_p50:.2f} ms |",
        f"| Latency p95 | {miracl_metrics.get('latency_p95_ms', 0):.2f} ms |",
        f"| Queries evaluated | {miracl_metrics.get('queries_evaluated', 0)} / 213 |",
        "",
        "---",
        "",
        "## EZIS Results (97 docs, 131 queries)",
        "",
    ]

    if e_ndcg is not None:
        lines += [
            "| Metric | Value |",
            "|--------|-------|",
            f"| NDCG@10 | **{e_ndcg:.4f}** |",
            f"| Recall@10 | {ezis_metrics.get('recall_at_10', 0):.4f} |",
            f"| MRR | {ezis_metrics.get('mrr', 0):.4f} |",
            f"| Latency p50 | {e_p50:.2f} ms |",
            f"| Latency p95 | {ezis_metrics.get('latency_p95_ms', 0):.2f} ms |",
            f"| Queries evaluated | {ezis_metrics.get('queries_evaluated', 0)} / 131 |",
        ]
    else:
        lines.append(f"_Error: {ezis_metrics.get('error')}_")

    lines += [
        "",
        "---",
        "",
        "## Phase Comparison (MIRACL-ko)",
        "",
        "| Phase | Method | NDCG@10 | p50 latency |",
        "|-------|--------|---------|-------------|",
        "| 2 | pg_textsearch + MeCab (BM25/WAND) | 0.3374 | - |",
        "| 3 | pgvector-sparse BM25 (kiwi-cong) | 0.6326 | 4.24ms |",
        "| 4 | BGE-M3 dense | 0.7915 | - |",
        "| 5 | pl/pgsql BM25 v2 (best) | - | 0.73ms |",
        f"| **6** | **VectorChord-BM25 + textsearch_ko** | **{m_ndcg:.4f}** | **{m_p50:.2f}ms** |",
        "",
        "---",
        "",
        "## Architecture",
        "",
        "```",
        "textsearch_ko (MeCab, main DB port 5432)",
        "    -> tsvector_to_array() -> Python vocab -> {id:count}::bm25vector",
        "VectorChord-BM25 (vchord-suite, port 5436)",
        "    CREATE INDEX <table>_emb_idx USING bm25 (emb bm25_ops)",
        "    SELECT id ORDER BY emb <&> to_bm25query('<table>_emb_idx', q::bm25vector)",
        "```",
        "",
    ]

    content = "\n".join(lines) + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def connect(db_url: str, label: str):
    try:
        conn = psycopg2.connect(db_url)
        conn.autocommit = False
        print(f"  [OK] {label}: {db_url}")
        return conn
    except Exception as e:
        print(f"  [FAIL] {label}: {e}")
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-url", default="postgresql://postgres:postgres@localhost:5436/dev")
    parser.add_argument("--main-db-url", default="postgresql://postgres:postgres@localhost:5432/dev")
    parser.add_argument("--output-dir", default="results/phase6")
    parser.add_argument("--skip-insert", action="store_true", help="Skip corpus insert (table already populated)")
    args = parser.parse_args()

    print("=" * 60)
    print("Phase 6-1: VectorChord-BM25 Full Evaluation (10K corpus)")
    print("=" * 60)

    conn_phase6 = connect(args.db_url, "phase6 DB (VectorChord-BM25)")
    conn_main = connect(args.main_db_url, "main DB (textsearch_ko)")

    if not conn_phase6 or not conn_main:
        print("[ERROR] Cannot connect. Is docker compose --profile phase6 up -d running?")
        sys.exit(1)

    # Load data
    print("\n[1/5] Loading corpus from text_embedding (main DB)...")
    docs = load_corpus_from_db(conn_main)
    queries = load_queries()
    print(f"  Corpus: {len(docs)} docs | Queries: {len(queries)}")

    # Verify coverage
    doc_id_set = {d["id"] for d in docs}
    valid_queries = [q for q in queries if any(r in doc_id_set for r in q["relevant_ids"])]
    print(f"  Valid queries (relevant docs in corpus): {len(valid_queries)}/{len(queries)}")

    # Build vocab
    print("\n[2/5] Building vocab via textsearch_ko (MeCab)...")
    texts = [d["text"] for d in docs]
    vocab = build_vocab(conn_main, texts)
    print(f"  Vocab size: {len(vocab)} terms")

    # --- MIRACL ---
    MIRACL_TABLE = "t6_miracl_10k"
    if not args.skip_insert:
        print("\n[3/6] Setting up phase6 DB (extensions + MIRACL table)...")
        setup_table(conn_phase6, MIRACL_TABLE, create_extension=True)

        print(f"\n[4/6] Inserting {len(docs)} MIRACL docs into VectorChord-BM25...")
        t0 = time.perf_counter()
        inserted = insert_corpus(conn_phase6, conn_main, docs, vocab, MIRACL_TABLE)
        elapsed = time.perf_counter() - t0
        print(f"  Throughput: {inserted / elapsed:.1f} docs/sec ({elapsed:.1f}s)")
        create_index(conn_phase6, MIRACL_TABLE)
    else:
        print("\n[3-4/6] Skipping MIRACL insert (--skip-insert)")
        with conn_phase6.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {MIRACL_TABLE}")
            print(f"  Existing rows: {cur.fetchone()[0]}")

    print(f"\n[5/6] Evaluating MIRACL ({len(valid_queries)} queries)...")
    miracl_metrics = run_evaluation(conn_phase6, conn_main, valid_queries, vocab, MIRACL_TABLE)
    if "error" in miracl_metrics:
        print(f"  [ERROR] {miracl_metrics['error']}")
    else:
        print(f"  NDCG@10:    {miracl_metrics['ndcg_at_10']:.4f}")
        print(f"  Recall@10:  {miracl_metrics['recall_at_10']:.4f}")
        print(f"  MRR:        {miracl_metrics['mrr']:.4f}")
        print(f"  p50 latency: {miracl_metrics['latency_p50_ms']:.2f} ms")
        print(f"  p95 latency: {miracl_metrics['latency_p95_ms']:.2f} ms")

    # --- EZIS ---
    print("\n[6/6] EZIS evaluation (97 docs, 131 queries)...")
    ezis_docs, ezis_queries = load_ezis()
    ezis_doc_ids = {d["id"] for d in ezis_docs}
    ezis_valid = [q for q in ezis_queries if any(r in ezis_doc_ids for r in q["relevant_ids"])]
    print(f"  Valid queries: {len(ezis_valid)}/{len(ezis_queries)}")

    EZIS_TABLE = "t6_ezis"
    ezis_texts = [d["text"] for d in ezis_docs]
    print("  Building EZIS vocab...")
    ezis_vocab = build_vocab(conn_main, ezis_texts, batch_size=97)

    setup_table(conn_phase6, EZIS_TABLE)
    insert_corpus(conn_phase6, conn_main, ezis_docs, ezis_vocab, EZIS_TABLE)
    create_index(conn_phase6, EZIS_TABLE)

    ezis_metrics = run_evaluation(conn_phase6, conn_main, ezis_valid, ezis_vocab, EZIS_TABLE)
    if "error" in ezis_metrics:
        print(f"  [ERROR] {ezis_metrics['error']}")
    else:
        print(f"  NDCG@10:    {ezis_metrics['ndcg_at_10']:.4f}")
        print(f"  Recall@10:  {ezis_metrics['recall_at_10']:.4f}")
        print(f"  MRR:        {ezis_metrics['mrr']:.4f}")
        print(f"  p50 latency: {ezis_metrics['latency_p50_ms']:.2f} ms")

    # Write report
    report_path = write_report(args.output_dir, len(docs), len(vocab), miracl_metrics, ezis_metrics)

    # Save JSON
    json_path = os.path.join(args.output_dir, "phase6_1_metrics.json")
    with open(json_path, "w") as f:
        json.dump({
            "miracl": {"corpus_size": len(docs), "vocab_size": len(vocab), **miracl_metrics},
            "ezis": {"corpus_size": len(ezis_docs), "vocab_size": len(ezis_vocab), **ezis_metrics},
        }, f, indent=2)

    print(f"\n  Report: {report_path}")
    print(f"  JSON:   {json_path}")

    for conn in [conn_phase6, conn_main]:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
