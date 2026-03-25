"""
Phase 8: Unified System Comparison Report

Merges results from:
  - PostgreSQL baseline (results/phase7/phase7_hybrid.json)
  - Elasticsearch (results/phase8/phase8_es.json)
  - Qdrant (results/phase8/phase8_qdrant.json)
  - Vespa (results/phase8/phase8_vespa.json, optional)

Output: results/phase8/phase8_system_comparison_report.md

Usage:
  uv run python3 experiments/phase8_system_comparison/phase8_report.py \\
    --output-dir results/phase8
"""

import argparse
import json
import os
from datetime import datetime
from typing import Dict, List, Optional


PG_BASELINE_PATH = "results/phase7/phase7_hybrid.json"
SYSTEM_LABEL = {
    "postgresql": "PostgreSQL (pg_textsearch+pgvector)",
    "elasticsearch": "Elasticsearch (nori)",
    "qdrant": "Qdrant 1.15.x",
    "vespa": "Vespa",
}


def load_json(path: str) -> Optional[dict]:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def pg_results_to_standard(pg_data: dict) -> List[dict]:
    """Convert Phase 7 PG results to standard format."""
    method_map = {
        "BM25":     "BM25",
        "Dense":    "Dense",
        "RRF":      "Hybrid",
        "Bayesian": "Hybrid-Bayesian",
    }
    out = []
    for r in pg_data.get("results", []):
        out.append({
            "system": "postgresql",
            "dataset": r["dataset"],
            "method": method_map.get(r["method"], r["method"]),
            "n_queries": r.get("n_queries"),
            "ndcg_at_10": r.get("ndcg_at_10"),
            "recall_at_10": r.get("recall_at_10"),
            "mrr": r.get("mrr"),
            "latency_p50": r.get("latency_p50"),
            "latency_p95": r.get("latency_p95"),
            "latency_p99": r.get("latency_p99"),
        })
    return out


def format_val(v, suffix="") -> str:
    if v is None:
        return "—"
    return f"{v}{suffix}"


def build_comparison_table(all_results: List[dict], dataset: str,
                           method: str) -> str:
    rows = [r for r in all_results
            if r["dataset"] == dataset and r["method"] == method]
    if not rows:
        return ""

    lines = [
        f"#### {dataset} — {method}",
        "",
        "| System | NDCG@10 | Recall@10 | MRR | p50 | p95 |",
        "|--------|---------|-----------|-----|-----|-----|",
    ]
    for r in rows:
        sys_label = SYSTEM_LABEL.get(r["system"], r["system"])
        lines.append(
            f"| {sys_label} "
            f"| {format_val(r['ndcg_at_10'])} "
            f"| {format_val(r['recall_at_10'])} "
            f"| {format_val(r['mrr'])} "
            f"| {format_val(r['latency_p50'], 'ms')} "
            f"| {format_val(r['latency_p95'], 'ms')} |"
        )
    lines.append("")
    return "\n".join(lines)


def generate_report(all_results: List[dict], output_dir: str) -> str:
    datasets = ["MIRACL", "EZIS"]
    methods  = ["BM25", "Dense", "Hybrid"]

    lines = [
        "# Phase 8: 시스템 비교 — 한국어 하이브리드 검색",
        "",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "**PostgreSQL baseline:** Phase 7 실측 (pg_textsearch BM25 + pgvector HNSW + DB-side RRF k=60)",
        "**Dense:** BGE-M3 1024-dim, retrieval-only (인퍼런스 제외)",
        "",
        "---",
        "",
        "## 비교 시스템",
        "",
        "| 시스템 | BM25 토크나이저 | Dense | 하이브리드 |",
        "|--------|--------------|-------|----------|",
        "| **PostgreSQL** | textsearch_ko MeCab (형태소) | pgvector HNSW | DB-side RRF SQL CTE |",
        "| **Elasticsearch** | nori (형태소, MeCab 계열) | dense_vector knn | ES RRF retriever |",
        "| **Qdrant 1.15.x** | MeCab sparse vector (IDF) | HNSW cosine | Qdrant prefetch RRF |",
        "| **Qdrant-builtin** | multilingual tokenizer (Unicode, 비형태소) | — | — |",
        "",
        "> Qdrant Text-builtin은 charabia Unicode word boundary — 형태소 분석 아님.",
        "> BM25 품질은 MeCab 기반보다 낮을 것으로 예상.",
        "",
        "---",
        "",
    ]

    for dataset in datasets:
        lines.append(f"## {dataset}")
        lines.append("")
        for method in methods:
            table = build_comparison_table(all_results, dataset, method)
            if table:
                lines.append(table)

    lines += [
        "---",
        "",
        "## 비고",
        "",
        "- **Dense latency**: 모든 시스템에서 retrieval-only (BGE-M3 인퍼런스 ~200ms 제외)",
        "- **Qdrant BM25-MeCab**: python-mecab-ko 외부 토크나이징 후 SparseVectorParams(modifier=IDF)",
        "- **Qdrant Text-builtin**: MatchText 페이로드 필터 (ranked BM25 아님, 스코어 없음)",
        "- **ES Hybrid**: `retriever.rrf` (rank_window_size=60, rank_constant=60)",
        "- **PG Hybrid**: DB-side SQL CTE RRF (k=60, topk=60)",
        "",
    ]

    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "phase8_system_comparison_report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  Report: {path}")
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="results/phase8")
    args = parser.parse_args()

    print("=" * 60)
    print("Phase 8: Unified System Comparison Report")
    print("=" * 60)

    all_results: List[dict] = []

    # PG baseline
    pg_data = load_json(PG_BASELINE_PATH)
    if pg_data:
        all_results += pg_results_to_standard(pg_data)
        print(f"  PG baseline: {len(pg_data.get('results', []))} records")
    else:
        print(f"  [WARN] PG baseline not found: {PG_BASELINE_PATH}")

    # External systems
    for system in ["es", "qdrant", "vespa"]:
        path = os.path.join(args.output_dir, f"phase8_{system}.json")
        data = load_json(path)
        if data:
            all_results += data.get("results", [])
            print(f"  {system}: {len(data.get('results', []))} records")
        else:
            print(f"  [skip] {path} not found")

    # Save merged JSON
    merged_path = os.path.join(args.output_dir, "phase8_system_comparison.json")
    with open(merged_path, "w", encoding="utf-8") as f:
        json.dump({
            "generated": datetime.now().isoformat(),
            "results": all_results,
        }, f, indent=2, ensure_ascii=False)
    print(f"  Merged JSON: {merged_path}")

    generate_report(all_results, args.output_dir)
    print("Done.")


if __name__ == "__main__":
    main()
