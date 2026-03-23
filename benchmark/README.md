# benchmark/

Evaluation framework for the Korean text search benchmark.

## Usage

### Run a benchmark

```python
from benchmark.runner import run_benchmark

def my_search(query: str) -> list[str]:
    # returns ranked list of doc_ids
    ...

queries = [
    {"query_id": "q1", "text": "검색 쿼리", "relevant_ids": {"doc_42", "doc_7"}},
]

results = run_benchmark(
    search_fn=my_search,
    queries=queries,
    method_name="bm25_kiwi",
    dataset_size=10000,
    output_path="results/bm25_kiwi_10k.json",
)
print(results["ndcg_at_10"], results["latency_p95_ms"])
```

### Compare methods

```python
from benchmark.report import load_results, generate_comparison_table

results = load_results("results/")
print(generate_comparison_table(results, output_path="results/comparison.md"))
```

### Generate charts

```python
from benchmark.report import plot_quality_comparison, plot_latency_scaling, plot_quality_vs_speed

results = load_results("results/")
plot_quality_comparison(results, output_path="results/quality.png")
plot_latency_scaling(results, output_path="results/latency.png")
plot_quality_vs_speed(results, output_path="results/tradeoff.png")
```

## Metrics

- **NDCG@10**: Normalized Discounted Cumulative Gain at rank 10 (primary metric)
- **Recall@1/5/10/20**: Fraction of relevant docs found in top-k
- **MRR**: Mean Reciprocal Rank
- **Latency p50/p95/p99**: Measured with `time.perf_counter()`, warmup queries discarded
- **Bootstrap CI**: 95% confidence intervals via resampling (1000 iterations)
