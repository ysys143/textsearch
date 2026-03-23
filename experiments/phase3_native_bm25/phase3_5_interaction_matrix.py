"""Phase 3.5: Cross-validation interaction matrix (BM25 impl x analyzer)."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    import pandas as pd
    _has_pd = True
except ImportError:
    pd = None  # type: ignore[assignment]
    _has_pd = False


def _to_records(results: Any) -> List[dict]:
    """Normalize a list-of-dicts or MultiIndex DataFrame to a list of dicts."""
    if hasattr(results, "reset_index") and hasattr(results, "to_dict"):
        return results.reset_index().to_dict("records")
    return list(results)


def _normalize_tokenizer_name(name: str) -> str:
    mapping = {
        "mecab": "Mecab",
        "okt": "Okt",
        "kiwi-cong": "kiwi-cong",
        "kiwi-knlm": "kiwi-knlm",
        "whitespace": "whitespace",
    }
    return mapping.get(name.lower(), name)


def _ndcg_at_k(ranked_ids: List, relevant_id, k: int = 10) -> float:
    for rank, doc_id in enumerate(ranked_ids[:k], start=1):
        if doc_id == relevant_id:
            return 1.0 / math.log2(rank + 1)
    return 0.0


def _run_single_cell(
    embedder,
    queries: List[Dict],
    docs: List[Dict],
    k: int = 10,
) -> Tuple[float, float, float, float, float]:
    """
    Run search for one (method, analyzer) cell.
    Returns (ndcg_mean, ci_lower, ci_upper, latency_p50_ms, latency_p95_ms).
    """
    try:
        import numpy as np
    except ImportError:
        raise RuntimeError("numpy is required")

    ndcg_scores = []
    latencies_ms = []

    for q in queries:
        query_text = q.get("text", q) if isinstance(q, dict) else q

        t0 = time.perf_counter()
        query_vec = embedder.embed_query(query_text)
        latencies_ms.append((time.perf_counter() - t0) * 1000)

        scored = []
        for doc in docs:
            doc_vec = embedder.embed_document(doc["text"])
            score = sum(query_vec.get(kid, 0.0) * v for kid, v in doc_vec.items())
            scored.append((score, doc["id"]))
        scored.sort(key=lambda x: -x[0])

        if scored and scored[0][0] > 0:
            top_id = scored[0][1]
            ranked_ids = [d for _, d in scored]
            ndcg_scores.append(_ndcg_at_k(ranked_ids, top_id, k))
        else:
            ndcg_scores.append(0.0)

    arr = np.array(ndcg_scores)
    mean_ndcg = float(np.mean(arr))

    # Bootstrap CI
    rng = np.random.default_rng(42)
    boot = [float(np.mean(rng.choice(arr, size=len(arr), replace=True))) for _ in range(1000)]
    ci_lower = float(np.percentile(boot, 2.5))
    ci_upper = float(np.percentile(boot, 97.5))

    lat = np.array(latencies_ms)
    p50 = float(np.percentile(lat, 50))
    p95 = float(np.percentile(lat, 95))

    return mean_ndcg, ci_lower, ci_upper, p50, p95


def run_interaction_matrix(
    conn,
    queries: List[Dict],
    docs: List[Dict],
    bm25_methods: List[str],
    analyzer_names: List[str],
) -> Any:
    """
    Run all combinations of bm25_method x analyzer.
    Returns pd.DataFrame with MultiIndex (bm25_method, analyzer) and columns:
        ndcg_at_10, ndcg_ci_lower, ndcg_ci_upper, latency_p50_ms, latency_p95_ms
    Falls back to a list of dicts if pandas is not installed.
    """
    if not _has_pd or pd is None:
        raise ImportError("pandas is required for run_interaction_matrix")


    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from src.bm25_module import BM25Embedder, SAMPLE_SENTENCES  # lazy import; use base class for in-memory dict scoring

    if not docs:
        docs = [{"id": i, "text": t} for i, t in enumerate(SAMPLE_SENTENCES)]

    corpus_texts = [d["text"] for d in docs]
    rows: List[dict] = []

    for method in bm25_methods:
        for analyzer in analyzer_names:
            print(f"[phase3.5] method={method}, analyzer={analyzer}")
            try:
                tokenizer_str = _normalize_tokenizer_name(analyzer)
                embedder = BM25Embedder(tokenizer=tokenizer_str)
                embedder.fit(corpus_texts)

                ndcg, ci_lo, ci_hi, p50, p95 = _run_single_cell(embedder, queries, docs)

                rows.append({
                    "bm25_method": method,
                    "analyzer": analyzer,
                    "ndcg_at_10": ndcg,
                    "ndcg_ci_lower": ci_lo,
                    "ndcg_ci_upper": ci_hi,
                    "latency_p50_ms": p50,
                    "latency_p95_ms": p95,
                })
                print(f"  ndcg@10={ndcg:.4f} [{ci_lo:.4f},{ci_hi:.4f}], p50={p50:.2f}ms, p95={p95:.2f}ms")

            except Exception as exc:
                print(f"  ERROR: {exc}")
                rows.append({
                    "bm25_method": method,
                    "analyzer": analyzer,
                    "ndcg_at_10": float("nan"),
                    "ndcg_ci_lower": float("nan"),
                    "ndcg_ci_upper": float("nan"),
                    "latency_p50_ms": float("nan"),
                    "latency_p95_ms": float("nan"),
                    "error": str(exc),
                })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.set_index(["bm25_method", "analyzer"])
    return df


def test_for_interaction_effects(results: Any) -> dict:
    """
    Check if BM25 method ranking is stable across analyzers.
    Accepts a list of dicts or a MultiIndex DataFrame from run_interaction_matrix.
    Returns {
        'interaction_detected': bool,
        'best_pair': (bm25_method, analyzer),
        'stable_bm25_ranking': bool,
    }
    """
    records = _to_records(results)
    # Group by analyzer -> rank bm25_methods
    from collections import defaultdict
    by_analyzer: Dict[str, List] = defaultdict(list)
    for row in records:
        if not math.isnan(row["ndcg_at_10"]):
            by_analyzer[row["analyzer"]].append((row["ndcg_at_10"], row["bm25_method"]))

    top_methods = []
    for analyzer, scores in by_analyzer.items():
        scores.sort(reverse=True)
        if scores:
            top_methods.append(scores[0][1])

    stable = len(set(top_methods)) == 1 if top_methods else True
    interaction_detected = not stable

    # Best pair overall
    valid = [r for r in records if not math.isnan(r["ndcg_at_10"])]
    if valid:
        best = max(valid, key=lambda r: r["ndcg_at_10"])
        best_pair = (best["bm25_method"], best["analyzer"])
    else:
        best_pair = (None, None)

    return {
        "interaction_detected": interaction_detected,
        "best_pair": best_pair,
        "stable_bm25_ranking": stable,
    }


def plot_interaction_heatmap(results: Any, output_path: Optional[str] = None):
    """
    Heatmap: rows=bm25_methods, cols=analyzers, color=ndcg_at_10.
    Annotate each cell with value. Save to output_path if given.
    Accepts a list of dicts or a MultiIndex DataFrame from run_interaction_matrix.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        print("matplotlib not available, skipping plot")
        return

    try:
        import seaborn as _sns
        _has_sns = True
    except ImportError:
        _sns = None
        _has_sns = False

    records = _to_records(results)
    methods = sorted(set(r["bm25_method"] for r in records))
    analyzers = sorted(set(r["analyzer"] for r in records))
    method_idx = {m: i for i, m in enumerate(methods)}
    analyzer_idx = {a: j for j, a in enumerate(analyzers)}

    matrix = np.full((len(methods), len(analyzers)), float("nan"))
    for row in records:
        i = method_idx[row["bm25_method"]]
        j = analyzer_idx[row["analyzer"]]
        matrix[i, j] = row["ndcg_at_10"]

    _fig, ax = plt.subplots(figsize=(max(6, len(analyzers) * 1.5), max(4, len(methods) * 1.2)))

    if _has_sns and _sns is not None and pd is not None:
        _df = pd.DataFrame(matrix, index=methods, columns=analyzers)
        _sns.heatmap(_df, annot=True, fmt=".3f", cmap="YlGnBu", linewidths=0.5,
                     cbar_kws={"label": "NDCG@10"}, ax=ax)
    else:
        im = ax.imshow(matrix, aspect="auto", cmap="YlGnBu")
        ax.set_xticks(range(len(analyzers)))
        ax.set_xticklabels(analyzers, rotation=45, ha="right")
        ax.set_yticks(range(len(methods)))
        ax.set_yticklabels(methods)
        for i in range(len(methods)):
            for j in range(len(analyzers)):
                val = matrix[i, j]
                if not math.isnan(val):
                    ax.text(j, i, f"{val:.3f}", ha="center", va="center", fontsize=9)
        plt.colorbar(im, ax=ax, label="NDCG@10")

    ax.set_title("BM25 Method × Analyzer Interaction Matrix (NDCG@10)")
    plt.tight_layout()

    if output_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        plt.savefig(output_path, dpi=150)
        print(f"[phase3.5] Heatmap saved to {output_path}")
    else:
        plt.show()
    plt.close()


def plot_quality_vs_speed_scatter(results: Any, output_path: Optional[str] = None):
    """Scatter: ndcg_at_10 (x) vs latency_p50_ms (y), each point labeled (method, analyzer).
    Accepts a list of dicts or a MultiIndex DataFrame from run_interaction_matrix.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not available, skipping plot")
        return

    records = _to_records(results)
    valid = [r for r in records if not math.isnan(r["ndcg_at_10"]) and not math.isnan(r["latency_p50_ms"])]
    if not valid:
        print("[phase3.5] No valid data for scatter plot.")
        return

    x = [r["ndcg_at_10"] for r in valid]
    y = [r["latency_p50_ms"] for r in valid]
    labels = [f"({r['bm25_method']}, {r['analyzer']})" for r in valid]

    _fig, ax = plt.subplots(figsize=(10, 6))
    ax.scatter(x, y, s=80, alpha=0.7)
    for xi, yi, label in zip(x, y, labels):
        ax.annotate(label, (xi, yi), textcoords="offset points", xytext=(5, 5), fontsize=8)

    ax.set_xlabel("NDCG@10")
    ax.set_ylabel("Latency p50 (ms)")
    ax.set_title("Quality vs Speed: BM25 Method × Analyzer")
    plt.tight_layout()

    if output_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        plt.savefig(output_path, dpi=150)
        print(f"[phase3.5] Scatter plot saved to {output_path}")
    else:
        plt.show()
    plt.close()


def main():
    parser = argparse.ArgumentParser(description="Phase 3.5: Interaction matrix")
    parser.add_argument("--db-url", required=True)
    parser.add_argument("--queries-file", required=True)
    parser.add_argument("--docs-file", required=True)
    parser.add_argument("--bm25-methods", nargs="+", default=["plpgsql", "pgvector-sparse"])
    parser.add_argument("--analyzers", nargs="+", default=["kiwi-cong", "mecab", "okt"])
    parser.add_argument("--output-dir", default="results/phase3_5")
    args = parser.parse_args()

    with open(args.queries_file, "r", encoding="utf-8") as f:
        queries = json.load(f)
    with open(args.docs_file, "r", encoding="utf-8") as f:
        docs = json.load(f)

    results = run_interaction_matrix(
        conn=None,
        queries=queries,
        docs=docs,
        bm25_methods=args.bm25_methods,
        analyzer_names=args.analyzers,
    )

    analysis = test_for_interaction_effects(results)
    print(f"\nInteraction detected: {analysis['interaction_detected']}")
    print(f"Best pair: {analysis['best_pair']}")
    print(f"Stable BM25 ranking: {analysis['stable_bm25_ranking']}")

    os.makedirs(args.output_dir, exist_ok=True)

    json_path = os.path.join(args.output_dir, "interaction_matrix.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"results": _to_records(results), "analysis": {
            "interaction_detected": analysis["interaction_detected"],
            "best_pair": list(analysis["best_pair"]),
            "stable_bm25_ranking": analysis["stable_bm25_ranking"],
        }}, f, ensure_ascii=False, indent=2)
    print(f"Results saved to {json_path}")

    plot_interaction_heatmap(results, os.path.join(args.output_dir, "interaction_heatmap.png"))
    plot_quality_vs_speed_scatter(results, os.path.join(args.output_dir, "quality_vs_speed.png"))


if __name__ == "__main__":
    main()
