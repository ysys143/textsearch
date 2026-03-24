"""Phase 5: Final system comparison — consolidates all phase results.

Reads results from phases 1-4 and generates a comprehensive comparison
report. For PostgreSQL latency comparison, re-runs top methods from
each phase against the same MIRACL query set.

Usage:
    uv run python3 experiments/phase5_system_comparison/phase5_final_report.py \
        --db-url postgresql://postgres:postgres@localhost:5432/dev \
        --output-dir results/phase5
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

import psycopg2
from pgvector.psycopg2 import register_vector


def mean(xs): return round(sum(xs) / len(xs), 4) if xs else 0.0
def pct(xs, p):
    s = sorted(xs); return round(s[int(len(s) * p / 100)], 2) if s else 0.0


# ---------------------------------------------------------------------------
# Load phase results
# ---------------------------------------------------------------------------

def load_phase_results() -> Dict:
    results = {}

    # Phase 1 — Python-side BM25 tokenizer comparison
    p1 = Path("results/phase1/phase1_analyzer_comparison.json")
    if p1.exists():
        d = json.loads(p1.read_text())
        results["phase1_miracl"] = d.get("miracl", [])
        results["phase1_ezis"]   = d.get("ezis", [])

    # Phase 2 — tsvector comparison
    p2 = Path("results/phase2/phase2_tsvector_comparison.json")
    if p2.exists():
        d = json.loads(p2.read_text())
        results["phase2_miracl"] = d.get("miracl", [])
        results["phase2_ezis"]   = d.get("ezis", [])

    # Phase 3 — pgvector-sparse BM25
    p3 = Path("results/phase3/phase3_bm25_comparison.json")
    if p3.exists():
        d = json.loads(p3.read_text())
        results["phase3_miracl"] = d.get("miracl", [])
        results["phase3_ezis"]   = d.get("ezis", [])

    # Phase 4 — BM25 vs neural sparse
    p4 = Path("results/phase4/phase4_comparison.json")
    if p4.exists():
        d = json.loads(p4.read_text())
        results["phase4_miracl"] = d.get("miracl", [])
        results["phase4_ezis"]   = d.get("ezis", [])

    return results


def best_by_ndcg(rows: List[Dict]) -> Optional[Dict]:
    valid = [r for r in rows if r.get("ndcg_at_10") is not None]
    return max(valid, key=lambda x: x["ndcg_at_10"]) if valid else None


# ---------------------------------------------------------------------------
# Fresh latency benchmark
# ---------------------------------------------------------------------------

def bench_latency(search_fn, queries: List[Dict], k: int = 10, n_warm: int = 5) -> Dict:
    """Run latency benchmark with warmup. Returns p50/p95 latencies."""
    # Warmup
    for q in queries[:n_warm]:
        search_fn(q["text"])
    lats = []
    for q in queries:
        rel = set(str(r) for r in q.get("relevant_ids", []))
        if not rel: continue
        t0 = time.perf_counter()
        search_fn(q["text"])
        lats.append((time.perf_counter() - t0) * 1000)
    return {"p50_ms": pct(lats, 50), "p95_ms": pct(lats, 95), "n": len(lats)}


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------

def render_report(all_results: Dict, latency_data: Dict) -> str:
    lines = ["# Phase 5: Korean Text Search Benchmark — Final Report\n",
             f"Generated: {time.strftime('%Y-%m-%d %H:%M')}\n",
             "## MIRACL-ko Results (10k corpus, 213 queries)\n",
             "| Phase | Method | NDCG@10 | R@10 | MRR | Latency p50 |",
             "|-------|--------|---------|------|-----|-------------|"]

    # Collect all MIRACL results
    miracl_rows = []
    for phase_key in ["phase1_miracl", "phase2_miracl", "phase3_miracl", "phase4_miracl"]:
        phase_num = phase_key.split("_")[0]
        for r in all_results.get(phase_key, []):
            if r.get("ndcg_at_10") is None: continue
            lat = r.get("latency_p50_ms", latency_data.get(r.get("method_id", ""), {}).get("p50_ms", "—"))
            miracl_rows.append((r.get("ndcg_at_10", 0), phase_num, r))

    for _, phase_num, r in sorted(miracl_rows, key=lambda x: -x[0]):
        method = r.get("method", r.get("tokenizer", "?"))
        ndcg = r.get("ndcg_at_10", 0)
        recall = r.get("recall_at_10", 0)
        mrr = r.get("mrr", 0)
        lat = r.get("latency_p50_ms", "—")
        lines.append(f"| {phase_num} | {method} | {ndcg:.4f} | {recall:.4f} | {mrr:.4f} | {lat}ms |")

    lines += ["",
              "## EZIS Results (97 docs, 131 queries)\n",
              "| Phase | Method | NDCG@10 | R@10 | MRR |",
              "|-------|--------|---------|------|-----|"]

    ezis_rows = []
    for phase_key in ["phase1_ezis", "phase2_ezis", "phase3_ezis", "phase4_ezis"]:
        phase_num = phase_key.split("_")[0]
        for r in all_results.get(phase_key, []):
            if r.get("ndcg_at_10") is None: continue
            ezis_rows.append((r.get("ndcg_at_10", 0), phase_num, r))

    for _, phase_num, r in sorted(ezis_rows, key=lambda x: -x[0]):
        method = r.get("method", r.get("tokenizer", "?"))
        ndcg = r.get("ndcg_at_10", 0)
        recall = r.get("recall_at_10", 0)
        mrr = r.get("mrr", 0)
        lines.append(f"| {phase_num} | {method} | {ndcg:.4f} | {recall:.4f} | {mrr:.4f} |")

    lines += ["",
              "## Key Findings\n",
              "### MIRACL-ko"]

    miracl_all = [r for k in ["phase1_miracl","phase2_miracl","phase3_miracl","phase4_miracl"]
                   for r in all_results.get(k, []) if r.get("ndcg_at_10")]
    if miracl_all:
        best = max(miracl_all, key=lambda x: x["ndcg_at_10"])
        method_name = best.get('method') or best.get('tokenizer') or best.get('method_id', '?')
        lines.append(f"- **Best method**: {method_name} — NDCG@10={best['ndcg_at_10']:.4f}")
        p1_best = best_by_ndcg(all_results.get("phase1_miracl", []))
        p3_best = best_by_ndcg(all_results.get("phase3_miracl", []))
        if p3_best and p1_best:
            gain = round((p3_best["ndcg_at_10"] - p1_best["ndcg_at_10"]) / max(p1_best["ndcg_at_10"], 1e-9) * 100, 1)
            lines.append(f"- pgvector-sparse (Phase 3) vs Python BM25 (Phase 1): {gain:+.1f}% NDCG gain from DB indexing")

    lines += ["", "### EZIS"]
    ezis_all = [r for k in ["phase1_ezis","phase2_ezis","phase3_ezis","phase4_ezis"]
                 for r in all_results.get(k, []) if r.get("ndcg_at_10")]
    if ezis_all:
        best = max(ezis_all, key=lambda x: x["ndcg_at_10"])
        method_name = best.get('method') or best.get('tokenizer') or best.get('method_id', '?')
        lines.append(f"- **Best method**: {method_name} — NDCG@10={best['ndcg_at_10']:.4f}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 5: Final comparison report")
    parser.add_argument("--db-url", default="postgresql://postgres:postgres@localhost:5432/dev")
    parser.add_argument("--output-dir", default="results/phase5")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    print("[Phase 5] Loading all phase results...")
    all_results = load_phase_results()
    for k, v in all_results.items():
        if v: print(f"  {k}: {len(v)} entries")

    # Quick latency re-run for top PostgreSQL methods
    print("\n[Phase 5] PostgreSQL latency re-benchmark (top methods)...")
    conn = psycopg2.connect(args.db_url)
    register_vector(conn)

    miracl_queries = [{"text": q["text"],
                        "relevant_ids": [str(r) for r in q.get("relevant_ids", [])]}
                      for q in json.load(open("data/miracl/queries_dev.json"))]

    latency_data = {}

    # pg_bigm latency
    print("  pg_bigm...")
    def bigm_search(q):
        with conn.cursor() as cur:
            cur.execute("SELECT id, bigm_similarity(text, %s) AS score "
                       "FROM phase2_miracl WHERE text LIKE %s "
                       "ORDER BY score DESC LIMIT 10",
                       (q, f"%{q}%"))
            return [r[0] for r in cur.fetchall()]
    latency_data["2-G"] = bench_latency(bigm_search, miracl_queries)

    # pgvector-sparse kiwi-cong latency
    print("  pgvector-sparse kiwi-cong...")
    from experiments.common.bm25_module import BM25Embedder_PG
    with conn.cursor() as cur:
        cur.execute("SELECT text FROM text_embedding ORDER BY id")
        corpus_texts = [r[0] for r in cur.fetchall()]
    emb_kiwi = BM25Embedder_PG(tokenizer="kiwi-cong")
    emb_kiwi.fit(corpus_texts)
    def kiwi_search(q):
        qv = emb_kiwi.embed_query(q)
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM text_embedding_sparse_bm25_kiwi_cong "
                       "ORDER BY emb_sparse <#> %s::sparsevec LIMIT 10", (qv,))
            return [r[0] for r in cur.fetchall()]
    latency_data["3-kiwi"] = bench_latency(kiwi_search, miracl_queries)

    conn.close()
    print(f"  pg_bigm: p50={latency_data['2-G']['p50_ms']:.1f}ms p95={latency_data['2-G']['p95_ms']:.1f}ms")
    print(f"  pgvector-sparse kiwi: p50={latency_data['3-kiwi']['p50_ms']:.1f}ms p95={latency_data['3-kiwi']['p95_ms']:.1f}ms")

    # Generate report
    print("\n[Phase 5] Generating report...")
    md = render_report(all_results, latency_data)
    md_path = os.path.join(args.output_dir, "phase5_final_report.md")
    Path(md_path).write_text(md, encoding="utf-8")
    print(f"  Saved: {md_path}")

    # Save JSON summary
    summary = {
        "latency_benchmarks": latency_data,
        "best_miracl": {},
        "best_ezis": {},
    }
    for phase in ["phase1", "phase2", "phase3", "phase4"]:
        for ds in ["miracl", "ezis"]:
            key = f"{phase}_{ds}"
            best = best_by_ndcg(all_results.get(key, []))
            if best:
                summary[f"best_{ds}"][phase] = {
                    "method": best.get("method", best.get("tokenizer", "?")),
                    "ndcg_at_10": best.get("ndcg_at_10"),
                    "method_id": best.get("method_id"),
                }

    json_path = os.path.join(args.output_dir, "phase5_summary.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"  Saved: {json_path}")

    # Print final summary
    print("\n" + "=" * 60)
    print("FINAL SUMMARY — MIRACL-ko:")
    for p, v in sorted(summary["best_miracl"].items()):
        print(f"  {p}: {v['method']:40} NDCG@10={v['ndcg_at_10']:.4f}")
    print("\nFINAL SUMMARY — EZIS:")
    for p, v in sorted(summary["best_ezis"].items()):
        print(f"  {p}: {v['method']:40} NDCG@10={v['ndcg_at_10']:.4f}")

    print(f"\nReport: {md_path}")


if __name__ == "__main__":
    main()
