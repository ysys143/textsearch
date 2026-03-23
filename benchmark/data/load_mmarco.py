"""Download and load mMARCO-ko (Korean MS MARCO) for benchmark evaluation."""

import argparse
import json
import os
from typing import Dict, List, Optional


def download_mmarco_ko(output_dir: str = "data/mmarco_ko", max_docs: Optional[int] = None) -> None:
    """Download mMARCO Korean subset and save to output_dir."""
    from datasets import load_dataset

    os.makedirs(output_dir, exist_ok=True)

    print("Loading mMARCO Korean collection (passages)...")
    collection = load_dataset("unicamp-dl/mmarco", "korean", split="collection")
    passages_path = os.path.join(output_dir, "passages.jsonl")
    count = 0
    with open(passages_path, "w", encoding="utf-8") as f:
        for item in collection:
            if max_docs is not None and count >= max_docs:
                break
            f.write(json.dumps({"id": item["id"], "text": item["text"]}, ensure_ascii=False) + "\n")
            count += 1
    print(f"Saved {count} passages to {passages_path}")

    print("Loading mMARCO Korean queries (dev)...")
    queries_ds = load_dataset("unicamp-dl/mmarco", "korean", split="queries")
    queries_path = os.path.join(output_dir, "queries.jsonl")
    with open(queries_path, "w", encoding="utf-8") as f:
        for item in queries_ds:
            f.write(json.dumps({"id": item["id"], "text": item["text"]}, ensure_ascii=False) + "\n")
    print(f"Saved queries to {queries_path}")

    print("Loading mMARCO qrels (dev)...")
    qrels_ds = load_dataset("unicamp-dl/mmarco", "korean", split="dev")
    qrels_path = os.path.join(output_dir, "qrels_dev.jsonl")
    with open(qrels_path, "w", encoding="utf-8") as f:
        for item in qrels_ds:
            f.write(json.dumps({
                "query_id": item["query_id"],
                "doc_id": item["doc_id"],
                "score": item.get("score", 1),
            }, ensure_ascii=False) + "\n")
    print(f"Saved qrels to {qrels_path}")


def get_eval_queries(
    data_dir: str = "data/mmarco_ko",
    split: str = "dev",
    max_queries: int = 200,
) -> List[Dict]:
    """
    Load evaluation queries with their relevant passage IDs.

    Returns:
        List of dicts: [{query_id, text, relevant_ids}]
    """
    queries_path = os.path.join(data_dir, "queries.jsonl")
    qrels_path = os.path.join(data_dir, f"qrels_{split}.jsonl")

    # Load queries
    queries: Dict[str, str] = {}
    with open(queries_path, "r", encoding="utf-8") as f:
        for line in f:
            item = json.loads(line)
            queries[str(item["id"])] = item["text"]

    # Load qrels: map query_id -> list of relevant doc_ids
    qrels: Dict[str, List[str]] = {}
    with open(qrels_path, "r", encoding="utf-8") as f:
        for line in f:
            item = json.loads(line)
            qid = str(item["query_id"])
            did = str(item["doc_id"])
            qrels.setdefault(qid, []).append(did)

    results: List[Dict] = []
    for qid, relevant_ids in list(qrels.items())[:max_queries]:
        if qid in queries:
            results.append({
                "query_id": qid,
                "text": queries[qid],
                "relevant_ids": set(relevant_ids),
            })

    return results


def get_passages(
    data_dir: str = "data/mmarco_ko",
    max_passages: Optional[int] = None,
) -> List[Dict]:
    """Load passages from saved JSONL file.

    Returns:
        List of dicts: [{doc_id, text}]
    """
    passages_path = os.path.join(data_dir, "passages.jsonl")
    results: List[Dict] = []
    with open(passages_path, "r", encoding="utf-8") as f:
        for line in f:
            if max_passages is not None and len(results) >= max_passages:
                break
            item = json.loads(line)
            results.append({"doc_id": str(item["id"]), "text": item["text"]})
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download mMARCO Korean dataset")
    parser.add_argument("--output-dir", default="data/mmarco_ko", help="Output directory")
    parser.add_argument("--max-queries", type=int, default=200, help="Maximum queries for eval")
    parser.add_argument("--max-docs", type=int, default=None, help="Maximum number of passages to save")
    args = parser.parse_args()
    download_mmarco_ko(output_dir=args.output_dir, max_docs=args.max_docs)
    queries = get_eval_queries(args.output_dir, max_queries=args.max_queries)
    print(f"Loaded {len(queries)} evaluation queries")
