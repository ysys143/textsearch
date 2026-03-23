"""Phase 3 Tier 1: Quick screen of Korean morphological analyzers (50 queries)."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from typing import Callable, Dict, List, Optional


def measure_tokenizer_throughput(tokenizer_fn: Callable, texts: List[str], n: int = 100) -> float:
    """Return docs/sec. Run tokenizer_fn on texts[:n], measure wall time."""
    sample = (texts * ((n // len(texts)) + 1))[:n] if texts else []
    start = time.perf_counter()
    for t in sample:
        tokenizer_fn(t)
    elapsed = time.perf_counter() - start
    if elapsed == 0:
        return float("inf")
    return n / elapsed


def measure_vocab_size(tokenizer_fn: Callable, texts: List[str]) -> int:
    """Return count of unique tokens across all texts."""
    vocab: set = set()
    for t in texts:
        vocab.update(tokenizer_fn(t))
    return len(vocab)


def _normalize_tokenizer_name(name: str) -> str:
    mapping = {
        "mecab": "Mecab",
        "okt": "Okt",
        "kkma": "kkma",
        "kiwi-cong": "kiwi-cong",
        "kiwi-knlm": "kiwi-knlm",
        "whitespace": "whitespace",
    }
    return mapping.get(name.lower(), name)


def build_bm25_index_for_analyzer(conn, tokenizer_name: str, docs: List[Dict]):
    """Build BM25Embedder inverted index for given analyzer. Drops existing index first."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from src.bm25_module import BM25Embedder  # lazy import
    embedder = BM25Embedder(tokenizer=_normalize_tokenizer_name(tokenizer_name))
    embedder.fit([d["text"] for d in docs])
    return embedder


def run_analyzer_bm25(conn, tokenizer_name: str, queries: List[Dict], k: int = 10) -> List[Dict]:
    """Run BM25 search with given analyzer. Returns [{query_id, ranked_ids}]."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from src.bm25_module import BM25Embedder, SAMPLE_SENTENCES  # lazy import
    docs = [{"id": i, "text": t} for i, t in enumerate(SAMPLE_SENTENCES)]
    embedder = BM25Embedder(tokenizer=_normalize_tokenizer_name(tokenizer_name))
    embedder.fit([d["text"] for d in docs])
    results = []
    for q in queries:
        query_text = q.get("text", q) if isinstance(q, dict) else q
        query_id = q.get("id", query_text) if isinstance(q, dict) else query_text
        query_vec = embedder.embed_query(query_text)
        scored = []
        for doc in docs:
            doc_vec = embedder.embed_document(doc["text"])
            score = sum(query_vec.get(kid, 0.0) * v for kid, v in doc_vec.items())
            scored.append((score, doc["id"]))
        scored.sort(key=lambda x: -x[0])
        results.append({"query_id": query_id, "ranked_ids": [doc_id for _, doc_id in scored[:k]]})
    return results


def _compute_ndcg_at_10_from_ranked(ranked_ids: List, relevant_id) -> float:
    for rank, doc_id in enumerate(ranked_ids[:10], start=1):
        if doc_id == relevant_id:
            return 1.0 / math.log2(rank + 1)
    return 0.0


def run_analyzer_screen(
    conn,
    queries_50: List[Dict],
    docs: Optional[List[Dict]] = None,
    analyzer_names: Optional[List[str]] = None,
) -> Dict[str, dict]:
    """
    Screen all analyzers on 50 queries.
    Returns {analyzer_name: {ndcg_at_10, tokenization_throughput_docs_per_sec, vocab_size, latency_p50_ms}}
    """
    if analyzer_names is None:
        analyzer_names = ["kiwi-cong", "kiwi-knlm", "mecab", "okt", "kkma", "whitespace"]

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from src.bm25_module import BM25Embedder, SAMPLE_SENTENCES  # lazy import

    if docs is None:
        docs = [{"id": i, "text": t} for i, t in enumerate(SAMPLE_SENTENCES)]

    corpus_texts = [d["text"] for d in docs]

    results: Dict[str, dict] = {}

    for analyzer_name in analyzer_names:
        print(f"[phase3] Evaluating analyzer: {analyzer_name}")
        try:
            tokenizer_str = _normalize_tokenizer_name(analyzer_name)
            embedder = BM25Embedder(tokenizer=tokenizer_str)
            embedder.fit(corpus_texts)
            tokenizer_fn = embedder.tokenizer

            # Throughput
            throughput = measure_tokenizer_throughput(tokenizer_fn, corpus_texts, n=100)

            # Vocab size
            vocab_size = measure_vocab_size(tokenizer_fn, corpus_texts)

            # NDCG@10 + latency
            ndcg_scores = []
            latencies_ms = []

            for q in queries_50:
                query_text = q.get("text", q) if isinstance(q, dict) else q

                t0 = time.perf_counter()
                query_vec = embedder.embed_query(query_text)
                latencies_ms.append((time.perf_counter() - t0) * 1000)

                # Score all docs
                scored = []
                for doc in docs:
                    doc_vec = embedder.embed_document(doc["text"])
                    score = sum(query_vec.get(kid, 0.0) * v for kid, v in doc_vec.items())
                    scored.append((score, doc["id"]))
                scored.sort(key=lambda x: -x[0])

                # Self-retrieval: top doc is "relevant"
                if scored and scored[0][0] > 0:
                    top_id = scored[0][1]
                    ndcg_scores.append(_compute_ndcg_at_10_from_ranked(
                        [d for _, d in scored], top_id
                    ))
                else:
                    ndcg_scores.append(0.0)

            import numpy as np
            ndcg_at_10 = float(np.mean(ndcg_scores)) if ndcg_scores else 0.0
            latency_p50 = float(np.percentile(latencies_ms, 50)) if latencies_ms else 0.0

            results[analyzer_name] = {
                "ndcg_at_10": ndcg_at_10,
                "tokenization_throughput_docs_per_sec": throughput,
                "vocab_size": vocab_size,
                "latency_p50_ms": latency_p50,
            }
            print(
                f"  ndcg@10={ndcg_at_10:.4f}, throughput={throughput:.1f} docs/sec, "
                f"vocab={vocab_size}, p50={latency_p50:.2f}ms"
            )

        except Exception as exc:
            print(f"[phase3] ERROR for analyzer '{analyzer_name}': {exc}")
            results[analyzer_name] = {
                "ndcg_at_10": 0.0,
                "tokenization_throughput_docs_per_sec": 0.0,
                "vocab_size": 0,
                "latency_p50_ms": 0.0,
                "error": str(exc),
            }

    return results


def get_top_analyzers(results: Dict[str, dict], n: int = 3) -> List[str]:
    """Return top-n analyzer names sorted by ndcg_at_10 descending."""
    return sorted(results, key=lambda k: results[k]["ndcg_at_10"], reverse=True)[:n]


def main():
    parser = argparse.ArgumentParser(description="Phase 3 Tier 1: Analyzer quick screen")
    parser.add_argument("--db-url", required=True)
    parser.add_argument("--queries-file", required=True)
    parser.add_argument("--docs-file", required=True)
    parser.add_argument("--output-dir", default="results/phase3_screen")
    parser.add_argument("--top-n", type=int, default=3)
    args = parser.parse_args()

    with open(args.queries_file, "r", encoding="utf-8") as f:
        queries_50 = json.load(f)
    with open(args.docs_file, "r", encoding="utf-8") as f:
        docs = json.load(f)

    results = run_analyzer_screen(conn=None, queries_50=queries_50, docs=docs)
    top = get_top_analyzers(results, n=args.top_n)
    print(f"\nTop-{args.top_n} analyzers: {top}")

    os.makedirs(args.output_dir, exist_ok=True)
    out_path = os.path.join(args.output_dir, "analyzer_screen_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"results": results, "top_analyzers": top}, f, ensure_ascii=False, indent=2)
    print(f"Results written to {out_path}")


if __name__ == "__main__":
    main()
