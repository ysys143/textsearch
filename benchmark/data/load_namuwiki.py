"""
Load and chunk Namuwiki documents for benchmarking.

Expects a directory of JSON or JSONL files where each record has:
  - 'title': article title (str)
  - 'text' or 'content': article body (str)

Long articles are split into overlapping chunks of min_chars to max_chars characters.
"""

import argparse
import glob
import json
import os
import random
from typing import Dict, List, Optional


def _iter_records(data_dir: str):
    """Yield raw records from all JSON/JSONL files in data_dir."""
    patterns = [
        os.path.join(data_dir, "*.jsonl"),
        os.path.join(data_dir, "*.json"),
    ]
    files = []
    for pattern in patterns:
        files.extend(glob.glob(pattern))

    if not files:
        raise FileNotFoundError(f"No JSON/JSONL files found in {data_dir}")

    for filepath in sorted(files):
        with open(filepath, "r", encoding="utf-8") as f:
            if filepath.endswith(".jsonl"):
                for line in f:
                    line = line.strip()
                    if line:
                        yield json.loads(line)
            else:
                data = json.load(f)
                if isinstance(data, list):
                    yield from data
                else:
                    yield data


def _chunk_text(text: str, min_chars: int, max_chars: int) -> List[str]:
    """
    Split text into chunks of min_chars to max_chars characters.
    Tries to split on newlines first, then falls back to hard splits.
    """
    if len(text) <= max_chars:
        if len(text) >= min_chars:
            return [text]
        return []

    chunks = []
    start = 0
    while start < len(text):
        end = start + max_chars
        if end >= len(text):
            chunk = text[start:]
            if len(chunk) >= min_chars:
                chunks.append(chunk)
            break

        # Try to find a good split point (newline) near the end of the window
        split_at = text.rfind("\n", start + min_chars, end)
        if split_at == -1:
            split_at = end

        chunk = text[start:split_at]
        if len(chunk) >= min_chars:
            chunks.append(chunk)
        start = split_at + 1 if text[split_at] == "\n" else split_at

    return chunks


def load_namuwiki_chunks(
    data_dir: str,
    min_chars: int = 1000,
    max_chars: int = 10000,
    max_docs: Optional[int] = None,
) -> List[Dict]:
    """
    Load Namuwiki articles and split into chunks.

    Args:
        data_dir: Directory containing JSON/JSONL files of Namuwiki articles.
        min_chars: Minimum characters per chunk (shorter chunks are discarded).
        max_chars: Maximum characters per chunk.
        max_docs: Maximum number of source articles to process.

    Returns:
        List of dicts: [{doc_id, text, title, char_len}]
        Filters to chunks with min_chars <= char_len <= max_chars.
    """
    results: List[Dict] = []
    doc_counter = 0

    for record in _iter_records(data_dir):
        if max_docs is not None and doc_counter >= max_docs:
            break

        title = record.get("title", "")
        text = record.get("text") or record.get("content") or ""

        if not text:
            continue

        chunks = _chunk_text(text, min_chars=min_chars, max_chars=max_chars)
        for i, chunk in enumerate(chunks):
            results.append({
                "doc_id": f"namuwiki_{doc_counter}_{i}",
                "text": chunk,
                "title": title,
                "char_len": len(chunk),
            })
        doc_counter += 1

    return results


def make_subsets(
    docs: List[Dict],
    sizes: List[int] = [1000, 10000, 50000, 100000],
) -> Dict[int, List[Dict]]:
    """Create reproducible subsets of docs at specified sizes. Uses random seed 42."""
    rng = random.Random(42)
    shuffled = docs[:]
    rng.shuffle(shuffled)
    subsets: Dict[int, List[Dict]] = {}
    for size in sizes:
        subsets[size] = shuffled[:size]
    return subsets


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load and chunk Namuwiki articles")
    parser.add_argument("--output-dir", default="data/namuwiki_chunks", help="Output directory")
    parser.add_argument("--input-dir", default="data/namuwiki_raw", help="Input directory with raw JSON/JSONL files")
    parser.add_argument("--max-docs", type=int, default=None, help="Maximum number of source articles to process")
    parser.add_argument("--min-chars", type=int, default=1000, help="Minimum characters per chunk")
    parser.add_argument("--max-chars", type=int, default=10000, help="Maximum characters per chunk")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    chunks = load_namuwiki_chunks(
        data_dir=args.input_dir,
        min_chars=args.min_chars,
        max_chars=args.max_chars,
    )
    if args.max_docs is not None:
        # Approximate: limit by source article count (doc_counter)
        seen_articles = set()
        filtered = []
        for chunk in chunks:
            article_id = "_".join(chunk["doc_id"].split("_")[:2])
            seen_articles.add(article_id)
            if len(seen_articles) <= args.max_docs:
                filtered.append(chunk)
        chunks = filtered

    output_path = os.path.join(args.output_dir, "chunks.jsonl")
    with open(output_path, "w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    print(f"Saved {len(chunks)} chunks to {output_path}")
