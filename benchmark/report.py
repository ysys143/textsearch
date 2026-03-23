"""Benchmark report generator."""
import json
from pathlib import Path
from typing import List, Optional


def load_results(results_dir: str) -> List[dict]:
    """Load all JSON files from results_dir."""
    results = []
    for p in Path(results_dir).glob("*.json"):
        with open(p, "r", encoding="utf-8") as f:
            results.append(json.load(f))
    return results


def generate_comparison_table(results: List[dict], output_path: Optional[str] = None) -> str:
    """Generate markdown table sorted by ndcg_at_10 desc."""
    sorted_results = sorted(results, key=lambda r: r.get("ndcg_at_10", 0.0), reverse=True)

    header = "| Method | Dataset Size | NDCG@10 | Recall@10 | MRR | P50 (ms) | P95 (ms) |"
    separator = "|--------|-------------|---------|-----------|-----|----------|----------|"
    rows = [header, separator]
    for r in sorted_results:
        row = (
            f"| {r.get('method', '')} "
            f"| {r.get('dataset_size', '')} "
            f"| {r.get('ndcg_at_10', 0.0):.4f} "
            f"| {r.get('recall_at_10', 0.0):.4f} "
            f"| {r.get('mrr', 0.0):.4f} "
            f"| {r.get('latency_p50_ms', 0.0):.2f} "
            f"| {r.get('latency_p95_ms', 0.0):.2f} |"
        )
        rows.append(row)

    table = "\n".join(rows)

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(table + "\n")

    return table


def plot_quality_comparison(results: List[dict], output_path: Optional[str] = None):
    """Bar chart: ndcg_at_10 per method, grouped by dataset_size. Save to output_path if given."""
    try:
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        return

    methods = sorted(set(r["method"] for r in results))
    sizes = sorted(set(r["dataset_size"] for r in results))

    x = np.arange(len(sizes))
    width = 0.8 / max(len(methods), 1)

    fig, ax = plt.subplots(figsize=(10, 6))
    for i, method in enumerate(methods):
        values = []
        for size in sizes:
            matching = [r for r in results if r["method"] == method and r["dataset_size"] == size]
            values.append(matching[0]["ndcg_at_10"] if matching else 0.0)
        ax.bar(x + i * width, values, width, label=method)

    ax.set_xlabel("Dataset Size")
    ax.set_ylabel("NDCG@10")
    ax.set_title("Quality Comparison by Method and Dataset Size")
    ax.set_xticks(x + width * (len(methods) - 1) / 2)
    ax.set_xticklabels([str(s) for s in sizes])
    ax.legend()
    fig.tight_layout()

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path)
    plt.close(fig)


def plot_latency_scaling(results: List[dict], output_path: Optional[str] = None):
    """Line chart: latency_p95_ms vs dataset_size per method."""
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return

    methods = sorted(set(r["method"] for r in results))

    fig, ax = plt.subplots(figsize=(10, 6))
    for method in methods:
        method_results = sorted(
            [r for r in results if r["method"] == method],
            key=lambda r: r["dataset_size"],
        )
        sizes = [r["dataset_size"] for r in method_results]
        p95s = [r["latency_p95_ms"] for r in method_results]
        ax.plot(sizes, p95s, marker="o", label=method)

    ax.set_xlabel("Dataset Size")
    ax.set_ylabel("P95 Latency (ms)")
    ax.set_title("Latency Scaling by Method")
    ax.legend()
    fig.tight_layout()

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path)
    plt.close(fig)


def plot_quality_vs_speed(results: List[dict], output_path: Optional[str] = None):
    """Scatter: ndcg_at_10 (x) vs latency_p50_ms (y), labeled by method."""
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return

    fig, ax = plt.subplots(figsize=(10, 6))
    for r in results:
        ax.scatter(r.get("ndcg_at_10", 0.0), r.get("latency_p50_ms", 0.0))
        ax.annotate(
            r.get("method", ""),
            (r.get("ndcg_at_10", 0.0), r.get("latency_p50_ms", 0.0)),
            textcoords="offset points",
            xytext=(5, 5),
            fontsize=8,
        )

    ax.set_xlabel("NDCG@10")
    ax.set_ylabel("P50 Latency (ms)")
    ax.set_title("Quality vs Speed")
    fig.tight_layout()

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path)
    plt.close(fig)
