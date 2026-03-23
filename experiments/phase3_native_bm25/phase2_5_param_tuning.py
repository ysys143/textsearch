"""
Phase 2.5: BM25 parameter tuning via grid search over k1 and b.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional

import pandas as pd

try:
    import psycopg2
except ImportError:
    psycopg2 = None  # type: ignore[assignment]

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.bm25_module import BM25Embedder_PG, bm25_sparse_search
from benchmark.runner import run_benchmark
from benchmark.eval import compute_ndcg, compute_recall, compute_mrr


def grid_search_bm25_params(
    conn,
    queries: List[Dict],
    k1_values: Optional[List[float]] = None,
    b_values: Optional[List[float]] = None,
    tokenizer: str = "mecab",
) -> pd.DataFrame:
    """
    Grid search over BM25 k1 and b parameters.
    Returns a DataFrame with columns: k1, b, ndcg_at_10, recall_at_10, mrr.
    """
    if k1_values is None:
        k1_values = [0.9, 1.2, 1.5]
    if b_values is None:
        b_values = [0.5, 0.75, 1.0]

    tokenizer_map = {"mecab": "Mecab", "kiwi-cong": "kiwi-cong", "okt": "Okt"}
    tok_name = tokenizer_map.get(tokenizer, tokenizer)

    with conn.cursor() as cur:
        cur.execute("SELECT text FROM text_embedding ORDER BY id;")
        corpus = [row[0] for row in cur.fetchall()]

    rows = []
    for k1 in k1_values:
        for b in b_values:
            embedder = BM25Embedder_PG(k=k1, b=b, tokenizer=tok_name)
            embedder.fit(corpus)

            ndcg_scores = []
            recall_scores = []
            mrr_scores = []

            for q in queries:
                query_text = q["text"] if isinstance(q, dict) else q
                relevant = set(q["relevant_ids"]) if isinstance(q, dict) else set()

                search_results = bm25_sparse_search(embedder, query_text, k=10)
                ranked_ids = [str(r[0]) for r in search_results]

                ndcg_scores.append(compute_ndcg(ranked_ids, relevant, k=10))
                recall_scores.append(compute_recall(ranked_ids, relevant, k=10))
                mrr_scores.append(compute_mrr(ranked_ids, relevant))

            n = len(ndcg_scores)
            rows.append(
                {
                    "k1": k1,
                    "b": b,
                    "ndcg_at_10": sum(ndcg_scores) / n if n else 0.0,
                    "recall_at_10": sum(recall_scores) / n if n else 0.0,
                    "mrr": sum(mrr_scores) / n if n else 0.0,
                }
            )
            print(
                f"  k1={k1}, b={b}  ndcg@10={rows[-1]['ndcg_at_10']:.4f}"
                f"  recall@10={rows[-1]['recall_at_10']:.4f}"
                f"  mrr={rows[-1]['mrr']:.4f}"
            )

    return pd.DataFrame(rows, columns=["k1", "b", "ndcg_at_10", "recall_at_10", "mrr"])


def find_best_params(results_df: pd.DataFrame) -> Dict[str, float]:
    """
    Return the parameter combination with the highest ndcg_at_10.
    Returns dict with keys: k1, b, ndcg.
    """
    best_row = results_df.loc[results_df["ndcg_at_10"].idxmax()]
    return {
        "k1": float(best_row["k1"]),
        "b": float(best_row["b"]),
        "ndcg": float(best_row["ndcg_at_10"]),
    }


def plot_param_heatmap(results, output_path: Optional[str] = None):
    """Heatmap: k1 (x) x b (y) colored by NDCG@10. Accepts DataFrame or list of dicts."""
    try:
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        print("[plot_param_heatmap] matplotlib/numpy not installed — skipping plot.")
        return

    if hasattr(results, "to_dict"):
        rows = results.to_dict("records")
    else:
        rows = results

    k1_vals = sorted(set(r["k1"] for r in rows))
    b_vals = sorted(set(r["b"] for r in rows))
    matrix = np.zeros((len(b_vals), len(k1_vals)))
    for r in rows:
        xi = k1_vals.index(r["k1"])
        yi = b_vals.index(r["b"])
        matrix[yi, xi] = r["ndcg_at_10"]

    _fig, ax = plt.subplots(figsize=(6, 5))
    im = ax.imshow(matrix, aspect="auto", origin="lower", cmap="YlOrRd")
    ax.set_xticks(range(len(k1_vals)))
    ax.set_xticklabels([str(v) for v in k1_vals])
    ax.set_yticks(range(len(b_vals)))
    ax.set_yticklabels([str(v) for v in b_vals])
    ax.set_xlabel("k1")
    ax.set_ylabel("b")
    ax.set_title("BM25 Parameter Grid Search — NDCG@10")
    plt.colorbar(im, ax=ax, label="NDCG@10")
    for i in range(len(b_vals)):
        for j in range(len(k1_vals)):
            ax.text(j, i, f"{matrix[i, j]:.3f}", ha="center", va="center", fontsize=8)
    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"[plot_param_heatmap] saved to {output_path}")
    else:
        plt.show()
    plt.close()


def main():
    parser = argparse.ArgumentParser(description="Phase 2.5: BM25 parameter tuning")
    parser.add_argument("--db-url", required=True, help="PostgreSQL connection URL")
    parser.add_argument("--queries-file", required=True, help="Path to queries JSON file")
    parser.add_argument("--output-dir", default="results", help="Output directory for results")
    parser.add_argument(
        "--tokenizer",
        choices=["mecab", "kiwi-cong", "okt"],
        default="mecab",
        help="Tokenizer for BM25Embedder",
    )
    parser.add_argument(
        "--k1-values",
        nargs="+",
        type=float,
        default=[0.9, 1.2, 1.5],
        help="k1 values to search",
    )
    parser.add_argument(
        "--b-values",
        nargs="+",
        type=float,
        default=[0.5, 0.75, 1.0],
        help="b values to search",
    )
    args = parser.parse_args()

    if psycopg2 is None:
        raise RuntimeError("psycopg2 is required. Install with: uv pip install psycopg2-binary")
    conn = psycopg2.connect(args.db_url)

    with open(args.queries_file, encoding="utf-8") as f:
        queries = json.load(f)

    os.makedirs(args.output_dir, exist_ok=True)

    print("Running BM25 grid search...")
    results_df = grid_search_bm25_params(
        conn=conn,
        queries=queries,
        k1_values=args.k1_values,
        b_values=args.b_values,
        tokenizer=args.tokenizer,
    )

    csv_path = os.path.join(args.output_dir, f"phase2_5_grid_search_{args.tokenizer}.csv")
    results_df.to_csv(csv_path, index=False)
    print(f"Grid search results saved to {csv_path}")

    best = find_best_params(results_df)
    print(f"Best params: k1={best['k1']}, b={best['b']}, ndcg@10={best['ndcg']:.4f}")

    best_path = os.path.join(args.output_dir, f"phase2_5_best_params_{args.tokenizer}.json")
    with open(best_path, "w", encoding="utf-8") as f:
        json.dump(best, f, indent=2)
    print(f"Best params saved to {best_path}")

    conn.close()


if __name__ == "__main__":
    main()
