"""Phase 2 A/B: textsearch_ko baseline (2-A) vs Enhanced (2-C) comparison.

Isolates tokenizer quality effect from ranking function by holding ts_rank_cd constant
and varying only the tokenizer (original vs OOV+VV하다+NNG compound+SL).

  2-A: original textsearch_ko (port 5434) — MeCab, no OOV passthrough
  2-C: enhanced textsearch_ko (port 5432) — OOV surface, VV+하다, NNG compound, SL

Usage:
    uv run python3 experiments/phase2_tsvector/phase2_textsearch_ko_ab.py \\
        --baseline-url postgresql://postgres:postgres@localhost:5434/dev \\
        --enhanced-url postgresql://postgres:postgres@localhost:5432/dev \\
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
from typing import Dict, List

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
    for i, doc_id in enumerate(ranked_ids):
        if doc_id in relevant_ids:
            return 1.0 / (i + 1)
    return 0.0


def mean(xs): return round(sum(xs) / len(xs), 4) if xs else 0.0
def pct(xs, p):
    s = sorted(xs)
    return round(s[int(len(s) * p / 100)], 2) if s else 0.0


# ---------------------------------------------------------------------------
# Setup: create documents table + tsvector config + index in a DB
# ---------------------------------------------------------------------------

def setup_db(conn, docs: List[Dict], config_name: str = "public.korean") -> None:
    """Create phase2_ab table with tsv column indexed by GIN."""
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS phase2_ab CASCADE")
        cur.execute("""
            CREATE TABLE phase2_ab (
                id TEXT PRIMARY KEY,
                text TEXT,
                tsv tsvector
            )
        """)
        psycopg2.extras.execute_batch(cur,
            "INSERT INTO phase2_ab (id, text) VALUES (%s, %s)",
            [(str(d["id"]), d["text"]) for d in docs],
            page_size=500)
        cur.execute(f"UPDATE phase2_ab SET tsv = to_tsvector('{config_name}', text)")
        cur.execute("CREATE INDEX idx_phase2_ab_tsv ON phase2_ab USING GIN(tsv)")
    conn.commit()


def korean_config_ddl(conn) -> None:
    """Create public.korean text search config using textsearch_ko extension."""
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS textsearch_ko")
        # Drop and recreate
        cur.execute("DROP TEXT SEARCH CONFIGURATION IF EXISTS public.korean CASCADE")
        cur.execute("DROP TEXT SEARCH DICTIONARY IF EXISTS public.korean_stem CASCADE")
        cur.execute("""
            CREATE TEXT SEARCH DICTIONARY public.korean_stem (
                TEMPLATE = mecabko
            )
        """)
        cur.execute("""
            CREATE TEXT SEARCH CONFIGURATION public.korean (
                PARSER = public.korean
            )
        """)
        # Morphological tokens → Korean stemmer
        for token_type in ("word", "hword", "hword_part"):
            cur.execute(
                f"ALTER TEXT SEARCH CONFIGURATION public.korean "
                f"ADD MAPPING FOR {token_type} WITH public.korean_stem"
            )
        # ASCII tokens → English stemmer
        for token_type in ("asciihword", "asciiword", "hword_asciipart"):
            cur.execute(
                f"ALTER TEXT SEARCH CONFIGURATION public.korean "
                f"ADD MAPPING FOR {token_type} WITH english_stem"
            )
        # Remaining token types → simple
        for token_type in ("numword", "url", "sfloat", "float", "int", "uint",
                           "version", "host", "url_path", "file", "hword_numpart",
                           "numhword", "email", "protocol", "tag", "entity"):
            try:
                cur.execute(
                    f"ALTER TEXT SEARCH CONFIGURATION public.korean "
                    f"ADD MAPPING FOR {token_type} WITH simple"
                )
            except Exception:
                conn.rollback()
                # token type may not exist in this parser — skip
    conn.commit()


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search_tsrank(conn, query_text: str, k: int = 10) -> List[str]:
    """ts_rank_cd search against phase2_ab table."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, ts_rank_cd(tsv, plainto_tsquery('public.korean', %s)) AS score
            FROM phase2_ab
            WHERE tsv @@ plainto_tsquery('public.korean', %s)
            ORDER BY score DESC
            LIMIT %s
        """, (query_text, query_text, k))
        return [str(r[0]) for r in cur.fetchall()]


def setup_bm25_index(conn) -> bool:
    """Create pg_textsearch BM25 index on phase2_ab. Returns True if successful."""
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS pg_textsearch")
            cur.execute("DROP INDEX IF EXISTS idx_phase2_ab_bm25")
            cur.execute("""
                CREATE INDEX idx_phase2_ab_bm25
                ON phase2_ab
                USING bm25(text)
                WITH (text_config='public.korean')
            """)
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        print(f"  WARNING: BM25 index creation failed: {e}")
        return False


def search_bm25(conn, query_text: str, k: int = 10) -> List[str]:
    """pg_textsearch BM25 search against phase2_ab table."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id FROM phase2_ab
            ORDER BY text <@> %s
            LIMIT %s
        """, (query_text, k))
        return [str(r[0]) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Evaluate
# ---------------------------------------------------------------------------

def evaluate(conn, queries: List[Dict], label: str, search_fn=None) -> Dict:
    if search_fn is None:
        search_fn = lambda q_text: search_tsrank(conn, q_text)
    ndcgs, recalls, mrrs, lats = [], [], [], []
    zero = 0

    for q in queries:
        rel = set(str(r) for r in q.get("relevant_ids", []))
        if not rel:
            continue
        t0 = time.perf_counter()
        ranked = search_fn(q["text"])
        lats.append((time.perf_counter() - t0) * 1000)

        if not ranked:
            zero += 1
            ndcgs.append(0.0); recalls.append(0.0); mrrs.append(0.0)
        else:
            ndcgs.append(ndcg_at_k(ranked, rel))
            recalls.append(recall_at_k(ranked, rel))
            mrrs.append(mrr_score(ranked, rel))

    return {
        "method": label,
        "ndcg_at_10": mean(ndcgs),
        "recall_at_10": mean(recalls),
        "mrr": mean(mrrs),
        "zero_rate": round(zero / len(queries), 3) if queries else 0,
        "latency_p50_ms": pct(lats, 50),
        "latency_p95_ms": pct(lats, 95),
        "n_queries": len(ndcgs),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-url", default="postgresql://postgres:postgres@localhost:5434/dev")
    parser.add_argument("--enhanced-url", default="postgresql://postgres:postgres@localhost:5432/dev")
    parser.add_argument("--miracl-docs", default="data/miracl/docs_ko_miracl.json")
    parser.add_argument("--miracl-queries", default="data/miracl/queries_dev.json")
    parser.add_argument("--ezis-chunks", default="data/ezis/chunks.json")
    parser.add_argument("--ezis-queries", default="data/ezis/queries.json")
    parser.add_argument("--output-dir", default="results/phase2")
    parser.add_argument("--dataset-size", type=int, default=1000)
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    # Load datasets
    print("[AB] Loading datasets...")
    miracl_docs = json.loads(Path(args.miracl_docs).read_text())[:args.dataset_size]
    miracl_queries = json.loads(Path(args.miracl_queries).read_text())
    ezis_docs = json.loads(Path(args.ezis_chunks).read_text())
    ezis_queries = json.loads(Path(args.ezis_queries).read_text())
    print(f"  MIRACL: {len(miracl_docs)} docs, {len(miracl_queries)} queries")
    print(f"  EZIS:   {len(ezis_docs)} docs, {len(ezis_queries)} queries")

    # Connect
    print(f"\n[AB] Connecting to baseline (port 5434)...")
    conn_base = psycopg2.connect(args.baseline_url)
    print(f"[AB] Connecting to enhanced (port 5432)...")
    conn_enh = psycopg2.connect(args.enhanced_url)

    results = {"miracl": [], "ezis": []}

    for dataset_name, docs, queries in [
        ("MIRACL", miracl_docs, miracl_queries),
        ("EZIS",   ezis_docs,   ezis_queries),
    ]:
        print(f"\n[{dataset_name}] Setting up baseline DB ({len(docs)} docs)...")
        try:
            korean_config_ddl(conn_base)
        except Exception as e:
            print(f"  WARNING: korean config setup: {e}")
            conn_base.rollback()
        setup_db(conn_base, docs)

        print(f"[{dataset_name}] Setting up enhanced DB ({len(docs)} docs)...")
        setup_db(conn_enh, docs)

        def _print(r):
            print(f"  NDCG@10={r['ndcg_at_10']:.4f}  R@10={r['recall_at_10']:.4f}"
                  f"  MRR={r['mrr']:.4f}  zero={r['zero_rate']:.3f}"
                  f"  p50={r['latency_p50_ms']}ms")

        # --- ts_rank_cd ---
        print(f"[{dataset_name}] Evaluating 2-A ts_rank_cd (baseline)...")
        r_base_ts = evaluate(conn_base, queries, "textsearch_ko baseline ts_rank_cd (2-A)")
        _print(r_base_ts)

        print(f"[{dataset_name}] Evaluating 2-C ts_rank_cd (enhanced)...")
        r_enh_ts = evaluate(conn_enh, queries, "textsearch_ko enhanced ts_rank_cd (2-C)")
        _print(r_enh_ts)

        # --- BM25 (pg_textsearch) ---
        print(f"[{dataset_name}] Building BM25 index on baseline...")
        base_bm25_ok = setup_bm25_index(conn_base)
        if base_bm25_ok:
            print(f"[{dataset_name}] Evaluating 2-A BM25 (baseline)...")
            r_base_bm25 = evaluate(conn_base, queries,
                                   "textsearch_ko baseline BM25 (2-A+BM25)",
                                   search_fn=lambda q, c=conn_base: search_bm25(c, q))
            _print(r_base_bm25)
        else:
            r_base_bm25 = None

        print(f"[{dataset_name}] Building BM25 index on enhanced...")
        enh_bm25_ok = setup_bm25_index(conn_enh)
        if enh_bm25_ok:
            print(f"[{dataset_name}] Evaluating 2-C BM25 (enhanced)...")
            r_enh_bm25 = evaluate(conn_enh, queries,
                                  "textsearch_ko enhanced BM25 (2-H-a)",
                                  search_fn=lambda q, c=conn_enh: search_bm25(c, q))
            _print(r_enh_bm25)
        else:
            r_enh_bm25 = None

        # Delta summary
        print(f"\n  [ts_rank_cd] Δ NDCG@10 = {r_enh_ts['ndcg_at_10'] - r_base_ts['ndcg_at_10']:+.4f}"
              f"  (baseline→enhanced)")
        if r_base_bm25 and r_enh_bm25:
            print(f"  [BM25]       Δ NDCG@10 = {r_enh_bm25['ndcg_at_10'] - r_base_bm25['ndcg_at_10']:+.4f}"
                  f"  (baseline→enhanced)")

        key = dataset_name.lower()
        results[key].extend([
            {**r_base_ts,   "phase": "phase2", "method_id": "2-A",      "ranking": "ts_rank_cd"},
            {**r_enh_ts,    "phase": "phase2", "method_id": "2-C",      "ranking": "ts_rank_cd"},
            *([{**r_base_bm25, "phase": "phase2", "method_id": "2-A+BM25", "ranking": "BM25"}] if r_base_bm25 else []),
            *([{**r_enh_bm25,  "phase": "phase2", "method_id": "2-H-a",    "ranking": "BM25"}] if r_enh_bm25 else []),
        ])

    conn_base.close()
    conn_enh.close()

    # Save
    out_path = os.path.join(args.output_dir, "phase2_textsearch_ko_ab.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n[AB] Saved: {out_path}")

    # Summary table
    print("\n" + "=" * 80)
    print("SUMMARY: textsearch_ko 2x2 — tokenizer (original/enhanced) × ranking (ts_rank_cd/BM25)")
    print("=" * 80)
    for dataset in ["miracl", "ezis"]:
        print(f"\n  {dataset.upper()}:")
        for r in results[dataset]:
            ranking = r.get("ranking", "ts_rank_cd")
            print(f"    [{r['method_id']:8}] [{ranking:10}]  NDCG@10={r['ndcg_at_10']:.4f}"
                  f"  R@10={r['recall_at_10']:.4f}  MRR={r['mrr']:.4f}  zero={r['zero_rate']}")


if __name__ == "__main__":
    main()
