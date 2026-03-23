"""Phase 1: 한국어 형태소 분석기 비교 벤치마크.

Python-side BM25(BM25Embedder) + 각 tokenizer 조합으로 MIRACL-ko, EZIS 두 데이터셋에서
검색 품질(NDCG@10, Recall@10, MRR)과 속도(throughput, latency)를 비교한다.

Usage:
    uv run python3 experiments/phase1_morphological/phase1_analyzer_comparison.py \
        --miracl-docs data/miracl/docs_ko_miracl.json \
        --miracl-queries data/miracl/queries_dev.json \
        --ezis-chunks data/ezis/chunks.json \
        --ezis-queries data/ezis/queries.json \
        --output-dir results/phase1
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from src.bm25_module import BM25Embedder  # noqa: E402

# ---------------------------------------------------------------------------
# Tokenizers to benchmark
# ---------------------------------------------------------------------------

TOKENIZERS = [
    ("whitespace", "whitespace"),
    ("mecab",      "Mecab"),
    ("okt",        "Okt"),
    ("kkma",       "Kkma"),
    ("kiwi-cong",  "kiwi-cong"),
]
# kiwi-knlm excluded: requires sj.knlm model file (not bundled)
# khaiii excluded: ARM64 build fails (no pre-built wheel for Apple Silicon)

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def ndcg_at_k(ranked_ids: List, relevant_ids: set, k: int = 10) -> float:
    """Compute NDCG@k for a single query."""
    dcg = 0.0
    for rank, doc_id in enumerate(ranked_ids[:k], start=1):
        if doc_id in relevant_ids:
            dcg += 1.0 / math.log2(rank + 1)
    # Ideal DCG: all relevant docs at top positions
    idcg = sum(1.0 / math.log2(i + 2) for i in range(min(len(relevant_ids), k)))
    return dcg / idcg if idcg > 0 else 0.0


def recall_at_k(ranked_ids: List, relevant_ids: set, k: int = 10) -> float:
    hits = sum(1 for d in ranked_ids[:k] if d in relevant_ids)
    return hits / len(relevant_ids) if len(relevant_ids) > 0 else 0.0


def mrr(ranked_ids: List, relevant_ids: set) -> float:
    for rank, doc_id in enumerate(ranked_ids, start=1):
        if doc_id in relevant_ids:
            return 1.0 / rank
    return 0.0


# ---------------------------------------------------------------------------
# BM25 scoring (pure Python)
# ---------------------------------------------------------------------------

def score_all_docs(
    embedder: BM25Embedder,
    query_text: str,
    doc_ids: List,
    doc_vecs: List[Dict],
) -> List[Tuple[float, any]]:
    """Return [(score, doc_id)] sorted descending."""
    q_vec = embedder.embed_query(query_text)
    if not q_vec:
        return [(0.0, did) for did in doc_ids]
    scored = []
    for did, d_vec in zip(doc_ids, doc_vecs):
        score = sum(q_vec.get(tok, 0.0) * w for tok, w in d_vec.items())
        scored.append((score, did))
    scored.sort(key=lambda x: -x[0])
    return scored


# ---------------------------------------------------------------------------
# Evaluation loop
# ---------------------------------------------------------------------------

def evaluate_tokenizer(
    tokenizer_label: str,
    tokenizer_str: str,
    docs: List[Dict],
    queries: List[Dict],
    k: int = 10,
) -> Dict:
    """Fit BM25 on docs, evaluate on queries. Returns metric dict."""
    print(f"  [{tokenizer_label}] fitting BM25 on {len(docs)} docs...", end="", flush=True)
    t_fit_start = time.perf_counter()
    try:
        embedder = BM25Embedder(tokenizer=tokenizer_str)
        corpus_texts = [d["text"] for d in docs]
        embedder.fit(corpus_texts)
    except Exception as e:
        print(f" FAILED: {e}")
        return {"error": str(e), "ndcg_at_10": 0.0, "recall_at_10": 0.0, "mrr": 0.0,
                "throughput_docs_per_sec": 0.0, "vocab_size": 0, "latency_p50_ms": 0.0}
    fit_time = time.perf_counter() - t_fit_start

    # Pre-compute doc vectors once
    doc_ids = [d["id"] for d in docs]
    doc_vecs = [embedder.embed_document(t) for t in corpus_texts]

    # Throughput: tokenize 200 docs
    sample = (corpus_texts * 3)[:200]
    t0 = time.perf_counter()
    for t in sample:
        embedder.tokenizer(t)
    throughput = len(sample) / max(time.perf_counter() - t0, 1e-9)

    # Vocab size
    vocab: set = set()
    for t in corpus_texts:
        vocab.update(embedder.tokenizer(t))

    ndcg_scores, recall_scores, mrr_scores, latencies = [], [], [], []
    for q in queries:
        q_text = q["text"]
        rel_ids = set(q.get("relevant_ids", []))
        if not rel_ids:
            continue
        t0 = time.perf_counter()
        scored = score_all_docs(embedder, q_text, doc_ids, doc_vecs)
        latencies.append((time.perf_counter() - t0) * 1000)
        ranked = [did for _, did in scored]
        ndcg_scores.append(ndcg_at_k(ranked, rel_ids, k))
        recall_scores.append(recall_at_k(ranked, rel_ids, k))
        mrr_scores.append(mrr(ranked, rel_ids))

    # Self-retrieval baseline: use each doc's own text as query → should rank #1
    # Measures vocabulary coverage / tokenizer discriminativeness
    self_retrieval_hits = 0
    for did, d_text in zip(doc_ids, corpus_texts):
        scored_self = score_all_docs(embedder, d_text, doc_ids, doc_vecs)
        top1_id = scored_self[0][1] if scored_self else None
        if top1_id == did:
            self_retrieval_hits += 1
    self_retrieval_at_1 = self_retrieval_hits / len(docs) if docs else 0.0

    def mean(xs): return sum(xs) / len(xs) if xs else 0.0
    def percentile(xs, p):
        if not xs: return 0.0
        xs_s = sorted(xs)
        idx = int(len(xs_s) * p / 100)
        return xs_s[min(idx, len(xs_s)-1)]

    result = {
        "tokenizer": tokenizer_label,
        "n_docs": len(docs),
        "n_queries": len(ndcg_scores),
        "ndcg_at_10": round(mean(ndcg_scores), 4),
        "recall_at_10": round(mean(recall_scores), 4),
        "mrr": round(mean(mrr_scores), 4),
        "throughput_docs_per_sec": round(throughput, 1),
        "vocab_size": len(vocab),
        "latency_p50_ms": round(percentile(latencies, 50), 2),
        "latency_p95_ms": round(percentile(latencies, 95), 2),
        "fit_time_sec": round(fit_time, 2),
        "self_retrieval_at_1": round(self_retrieval_at_1, 4),
    }
    print(f" NDCG@10={result['ndcg_at_10']:.4f}  R@10={result['recall_at_10']:.4f}"
          f"  MRR={result['mrr']:.4f}  tput={result['throughput_docs_per_sec']:.0f}/s"
          f"  vocab={result['vocab_size']}")
    return result


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def render_markdown_table(results: List[Dict], dataset_name: str) -> str:
    lines = [f"\n## {dataset_name}\n"]
    header = "| Tokenizer | NDCG@10 | Recall@10 | MRR | Self-R@1 | Throughput (docs/s) | Vocab | p50 ms |"
    sep    = "|-----------|---------|-----------|-----|----------|---------------------|-------|--------|"
    lines += [header, sep]
    for r in sorted(results, key=lambda x: -x.get("ndcg_at_10", 0)):
        if "error" in r:
            lines.append(f"| {r['tokenizer']} | ERROR | — | — | — | — | — | — |")
        else:
            lines.append(
                f"| {r['tokenizer']} | {r['ndcg_at_10']:.4f} | {r['recall_at_10']:.4f}"
                f" | {r['mrr']:.4f} | {r.get('self_retrieval_at_1', 0):.4f}"
                f" | {r['throughput_docs_per_sec']:.0f}"
                f" | {r['vocab_size']} | {r['latency_p50_ms']:.1f} |"
            )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 1: 형태소 분석기 비교 벤치마크")
    parser.add_argument("--miracl-docs",    default="data/miracl/docs_ko_miracl.json")
    parser.add_argument("--miracl-queries", default="data/miracl/queries_dev.json")
    parser.add_argument("--ezis-chunks",    default="data/ezis/chunks.json")
    parser.add_argument("--ezis-queries",   default="data/ezis/queries.json")
    parser.add_argument("--output-dir",     default="results/phase1")
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--skip-kkma", action="store_true", help="Skip Kkma (slow JVM)")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    tokenizers = [(lbl, ts) for lbl, ts in TOKENIZERS if not (args.skip_kkma and lbl == "kkma")]

    # Load datasets
    print("[Phase 1] Loading datasets...")
    # Normalize all doc IDs and relevant_ids to strings to avoid int/str mismatch
    miracl_docs    = [{"id": str(d["id"]), "text": d["text"]}
                      for d in json.load(open(args.miracl_docs))]
    miracl_queries = [{"query_id": q["query_id"], "text": q["text"],
                       "relevant_ids": [str(r) for r in q.get("relevant_ids", [])]}
                      for q in json.load(open(args.miracl_queries))]
    ezis_docs      = [{"id": str(c["id"]), "text": c["text"]}
                      for c in json.load(open(args.ezis_chunks))]
    ezis_queries   = [{"query_id": q["query_id"], "text": q["text"],
                       "relevant_ids": [str(r) for r in q.get("relevant_ids", [])]}
                      for q in json.load(open(args.ezis_queries))]

    print(f"  MIRACL:  {len(miracl_docs)} docs, {len(miracl_queries)} queries")
    print(f"  EZIS:    {len(ezis_docs)} docs, {len(ezis_queries)} queries")

    # ---- MIRACL ----
    print("\n[MIRACL-ko] Starting evaluation...")
    miracl_results = []
    for label, tok_str in tokenizers:
        r = evaluate_tokenizer(label, tok_str, miracl_docs, miracl_queries, k=args.k)
        r["dataset"] = "miracl"
        miracl_results.append(r)

    # ---- EZIS ----
    print("\n[EZIS] Starting evaluation...")
    ezis_results = []
    for label, tok_str in tokenizers:
        r = evaluate_tokenizer(label, tok_str, ezis_docs, ezis_queries, k=args.k)
        r["dataset"] = "ezis"
        ezis_results.append(r)

    # Save JSON
    output = {"miracl": miracl_results, "ezis": ezis_results}
    json_path = os.path.join(args.output_dir, "phase1_analyzer_comparison.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n[Phase 1] Results saved: {json_path}")

    # Save Markdown
    md_lines = ["# Phase 1: 형태소 분석기 비교 결과\n",
                f"Generated: {time.strftime('%Y-%m-%d %H:%M')}\n",
                "Tokenizers: " + ", ".join(lbl for lbl, _ in tokenizers)]
    md_lines.append(render_markdown_table(miracl_results, "MIRACL-ko (213 queries, 1000 docs)"))
    md_lines.append(render_markdown_table(ezis_results, "EZIS Oracle Manual (131 queries, 97 docs)"))

    md_lines.append("\n## 분석\n")
    # Top-3 by NDCG on MIRACL
    top3_miracl = sorted([r for r in miracl_results if "error" not in r],
                         key=lambda x: -x["ndcg_at_10"])[:3]
    top3_ezis = sorted([r for r in ezis_results if "error" not in r],
                       key=lambda x: -x["ndcg_at_10"])[:3]
    md_lines.append(f"**MIRACL top-3:** {', '.join(r['tokenizer'] for r in top3_miracl)}")
    md_lines.append(f"\n**EZIS top-3:** {', '.join(r['tokenizer'] for r in top3_ezis)}")
    md_lines.append("\n→ Phase 2, 3에서 사용할 형태소 분석기: top-3 교집합 우선.\n")

    md_path = os.path.join(args.output_dir, "phase1_analyzer_comparison.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))
    print(f"[Phase 1] Report saved: {md_path}")

    # Console summary
    print("\n" + "=" * 60)
    print("MIRACL-ko top results:")
    for r in sorted([x for x in miracl_results if "error" not in x], key=lambda x: -x["ndcg_at_10"])[:5]:
        print(f"  {r['tokenizer']:15} NDCG@10={r['ndcg_at_10']:.4f}  MRR={r['mrr']:.4f}")
    print("\nEZIS top results:")
    for r in sorted([x for x in ezis_results if "error" not in x], key=lambda x: -x["ndcg_at_10"])[:5]:
        print(f"  {r['tokenizer']:15} NDCG@10={r['ndcg_at_10']:.4f}  MRR={r['mrr']:.4f}")


if __name__ == "__main__":
    main()
