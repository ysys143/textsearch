"""Benchmark runner."""
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Dict, Any, Optional

from benchmark.eval import compute_ndcg, compute_recall, compute_mrr, measure_latency, bootstrap_ci


def run_benchmark(
    search_fn: Callable[[str], List[str]],
    queries: List[Dict],  # [{query_id, text, relevant_ids: set or list}]
    method_name: str,
    dataset_size: int,
    output_path: Optional[str] = None,
    latency_warmup: int = 10,
) -> Dict[str, Any]:
    """
    Run search_fn on all queries. Compute all metrics. Optionally save JSON.
    Returns the results dict.
    """
    ndcg_scores = []
    recall1_scores = []
    recall5_scores = []
    recall10_scores = []
    recall20_scores = []
    mrr_scores = []

    for q in queries:
        relevant = set(q["relevant_ids"])
        ranked = search_fn(q["text"])
        ndcg_scores.append(compute_ndcg(ranked, relevant, k=10))
        recall1_scores.append(compute_recall(ranked, relevant, k=1))
        recall5_scores.append(compute_recall(ranked, relevant, k=5))
        recall10_scores.append(compute_recall(ranked, relevant, k=10))
        recall20_scores.append(compute_recall(ranked, relevant, k=20))
        mrr_scores.append(compute_mrr(ranked, relevant))

    ndcg_ci = bootstrap_ci(ndcg_scores)
    recall10_ci = bootstrap_ci(recall10_scores)
    mrr_ci = bootstrap_ci(mrr_scores)

    query_texts = [q["text"] for q in queries]
    latency = measure_latency(search_fn, query_texts, warmup=latency_warmup)

    import statistics as _stats

    result = {
        "method": method_name,
        "dataset_size": dataset_size,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "num_queries": len(queries),
        "n_queries": len(queries),
        "ndcg_at_10": ndcg_ci["mean"],
        "ndcg_ci_lower": ndcg_ci["lower"],
        "ndcg_ci_upper": ndcg_ci["upper"],
        "ndcg_at_10_lower": ndcg_ci["lower"],
        "ndcg_at_10_upper": ndcg_ci["upper"],
        "recall_at_1": _stats.mean(recall1_scores),
        "recall_at_5": _stats.mean(recall5_scores),
        "recall_at_10": recall10_ci["mean"],
        "recall_at_10_lower": recall10_ci["lower"],
        "recall_at_10_upper": recall10_ci["upper"],
        "recall_at_20": _stats.mean(recall20_scores),
        "mrr": mrr_ci["mean"],
        "mrr_lower": mrr_ci["lower"],
        "mrr_upper": mrr_ci["upper"],
        "latency_p50_ms": latency["p50"],
        "latency_p95_ms": latency["p95"],
        "latency_p99_ms": latency["p99"],
        "per_query": [
            {
                "query_id": q.get("query_id", i),
                "ndcg_at_10": ndcg_scores[i],
                "recall_at_1": recall1_scores[i],
                "recall_at_5": recall5_scores[i],
                "recall_at_10": recall10_scores[i],
                "recall_at_20": recall20_scores[i],
                "mrr": mrr_scores[i],
            }
            for i, q in enumerate(queries)
        ],
    }

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    return result
