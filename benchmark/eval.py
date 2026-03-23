"""Korean text search benchmark evaluation metrics (TREC conventions)."""
import numpy as np
import time
from typing import List, Dict, Any, Callable, Set


def compute_ndcg(ranked_ids: List[str], relevant_ids: Set[str], k: int = 10) -> float:
    """NDCG@k. Binary relevance: 1 if in relevant_ids, 0 otherwise."""
    if not relevant_ids:
        return 0.0
    top_k = ranked_ids[:k]
    dcg = sum(
        (1.0 / np.log2(i + 2)) for i, doc_id in enumerate(top_k) if doc_id in relevant_ids
    )
    n_relevant = min(len(relevant_ids), k)
    idcg = sum(1.0 / np.log2(i + 2) for i in range(n_relevant))
    if idcg == 0.0:
        return 0.0
    return dcg / idcg


def compute_recall(ranked_ids: List[str], relevant_ids: Set[str], k: int) -> float:
    """Recall@k: |relevant in top-k| / |relevant|. Return 0.0 if no relevant."""
    if not relevant_ids:
        return 0.0
    top_k = set(ranked_ids[:k])
    return len(top_k & relevant_ids) / len(relevant_ids)


def compute_mrr(ranked_ids: List[str], relevant_ids: Set[str]) -> float:
    """MRR: 1/rank of first relevant result. Return 0.0 if none found."""
    for i, doc_id in enumerate(ranked_ids):
        if doc_id in relevant_ids:
            return 1.0 / (i + 1)
    return 0.0


def measure_latency(search_fn: Callable, queries: List[str], warmup: int = 10) -> Dict[str, float]:
    """
    Run search_fn(query) for each query. First `warmup` results discarded.
    Use time.perf_counter() for timing. Return {p50, p95, p99} in milliseconds.
    """
    latencies = []
    for i, query in enumerate(queries):
        start = time.perf_counter()
        search_fn(query)
        elapsed = (time.perf_counter() - start) * 1000.0  # ms
        if i >= warmup:
            latencies.append(elapsed)
    if not latencies:
        # All were warmup — include all
        latencies = []
        for query in queries:
            start = time.perf_counter()
            search_fn(query)
            elapsed = (time.perf_counter() - start) * 1000.0
            latencies.append(elapsed)
    arr = np.array(latencies)
    return {
        "p50": float(np.percentile(arr, 50)),
        "p95": float(np.percentile(arr, 95)),
        "p99": float(np.percentile(arr, 99)),
    }


def bootstrap_ci(scores: List[float], n_iterations: int = 1000, ci: float = 0.95) -> Dict[str, float]:
    """
    Bootstrap CI via resampling with replacement.
    Return {mean, lower, upper, std}.
    lower/upper are (1-ci)/2 and (1+ci)/2 percentiles.
    """
    arr = np.array(scores)
    rng = np.random.default_rng(42)
    boot_means = np.array([
        rng.choice(arr, size=len(arr), replace=True).mean()
        for _ in range(n_iterations)
    ])
    lower_p = (1.0 - ci) / 2.0 * 100.0
    upper_p = (1.0 + ci) / 2.0 * 100.0
    return {
        "mean": float(arr.mean()),
        "lower": float(np.percentile(boot_means, lower_p)),
        "upper": float(np.percentile(boot_means, upper_p)),
        "std": float(arr.std()),
    }


def bootstrap_test(scores_a: List[float], scores_b: List[float], n_iterations: int = 1000) -> Dict[str, float]:
    """
    Two-sided bootstrap significance test.
    H0: mean(A) == mean(B).
    """
    a = np.array(scores_a, dtype=float)
    b = np.array(scores_b, dtype=float)
    rng = np.random.default_rng(42)

    observed_diff = a.mean() - b.mean()
    combined = np.concatenate([a, b])
    grand_mean = combined.mean()

    shifted_a = a - a.mean() + grand_mean
    shifted_b = b - b.mean() + grand_mean

    count = 0
    diffs = []
    for _ in range(n_iterations):
        bs_a = rng.choice(shifted_a, size=len(shifted_a), replace=True)
        bs_b = rng.choice(shifted_b, size=len(shifted_b), replace=True)
        diff = bs_a.mean() - bs_b.mean()
        diffs.append(diff)
        if abs(diff) >= abs(observed_diff):
            count += 1

    p_value = count / n_iterations
    diffs_arr = np.array(diffs)
    ci_lower = float(np.percentile(diffs_arr, 2.5))
    ci_upper = float(np.percentile(diffs_arr, 97.5))

    return {
        "p_value": float(p_value),
        "mean_diff": float(observed_diff),
        "ci_lower": ci_lower,
        "ci_upper": ci_upper,
    }
