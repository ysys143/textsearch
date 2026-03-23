"""Phase 0-B: EZIS Oracle Manual PDF → chunks + QA set.

Pipeline:
  1. Parse PDF pages (pdfplumber)
  2. Detect section boundaries and chunk
  3. Generate hard questions per chunk via Claude API
  4. Output MIRACL-compatible JSON files

Usage:
  # Dry-run (parse + chunk only, no LLM calls):
  uv run python3 benchmark/data/load_ezis.py --dry-run --pdf data/EZIS_Oracle_Manual.pdf

  # Full pipeline (chunk 10 only):
  uv run python3 benchmark/data/load_ezis.py --pdf data/EZIS_Oracle_Manual.pdf --max-chunks 10 --output-dir data/ezis

  # Full pipeline:
  uv run python3 benchmark/data/load_ezis.py --pdf data/EZIS_Oracle_Manual.pdf --output-dir data/ezis
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Iterator

try:
    import pdfplumber
except ImportError:
    raise ImportError("pdfplumber required: uv pip install pdfplumber")

try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Section boundary detection
# ---------------------------------------------------------------------------

# Matches "1. ", "2.1. ", "2.1 ", "13. " at the start of a line
_SECTION_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,2})?\.?\s+\S)")


def _is_section_header(line: str) -> bool:
    return bool(_SECTION_RE.match(line.strip()))


def _extract_section_label(line: str) -> str:
    """Return '2.1' from '2.1. Users > Users ...' etc."""
    m = re.match(r"^(\d{1,2}(?:\.\d{1,2})?)", line.strip())
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# Step 1: PDF parsing
# ---------------------------------------------------------------------------

def parse_pdf(pdf_path: str) -> list[dict]:
    """Extract text from each page, skipping blank/cover pages."""
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text()
            if text and len(text.strip()) > 50:
                pages.append({"page": i, "text": text.strip()})
    return pages


# ---------------------------------------------------------------------------
# Step 2: Chunking
# ---------------------------------------------------------------------------

def chunk_by_section(
    pages: list[dict],
    min_chars: int = 200,
    max_chars: int = 2000,
    overlap_chars: int = 100,
) -> list[dict]:
    """Split pages into chunks using section headers as primary boundaries.

    Strategy:
    - Accumulate lines until a new section header is found → emit chunk
    - If accumulated text exceeds max_chars, apply sliding window
    - Merge chunks shorter than min_chars into the next one
    """
    chunks: list[dict] = []
    current_lines: list[str] = []
    current_section: str = ""
    page_start: int = 1

    def _emit(lines: list[str], section: str, p_start: int, p_end: int) -> None:
        text = "\n".join(lines).strip()
        if len(text) < min_chars:
            return
        # Sliding window if too long
        if len(text) <= max_chars:
            chunks.append({
                "id": f"ezis_{len(chunks)}",
                "text": text,
                "section": section,
                "page_start": p_start,
                "page_end": p_end,
            })
        else:
            # Split into overlapping windows
            words = text.split()
            window: list[str] = []
            w_start = 0
            while w_start < len(words):
                window = words[w_start:]
                window_text = " ".join(window)
                if len(window_text) <= max_chars or len(window) <= 50:
                    if len(window_text) >= min_chars:
                        chunks.append({
                            "id": f"ezis_{len(chunks)}",
                            "text": window_text,
                            "section": section,
                            "page_start": p_start,
                            "page_end": p_end,
                        })
                    break
                # Find a cut point around max_chars
                cut_text = " ".join(window[:200])
                chunks.append({
                    "id": f"ezis_{len(chunks)}",
                    "text": " ".join(window[:200]),
                    "section": section,
                    "page_start": p_start,
                    "page_end": p_end,
                })
                # Overlap: step back by overlap_chars worth of words (~10 words avg)
                step = max(150, 200 - overlap_chars // 5)
                w_start += step

    current_page_end = 1

    for page in pages:
        lines = page["text"].splitlines()
        for line in lines:
            if _is_section_header(line):
                # Emit previous chunk
                if current_lines:
                    _emit(current_lines, current_section, page_start, current_page_end)
                    current_lines = []
                current_section = _extract_section_label(line)
                page_start = page["page"]
            current_lines.append(line)
        current_page_end = page["page"]

    if current_lines:
        _emit(current_lines, current_section, page_start, current_page_end)

    # Merge tiny trailing chunks into previous
    merged: list[dict] = []
    for chunk in chunks:
        if merged and len(chunk["text"]) < min_chars:
            merged[-1]["text"] += "\n" + chunk["text"]
            merged[-1]["page_end"] = chunk["page_end"]
        else:
            merged.append(chunk)

    # Re-index IDs after merge
    for i, c in enumerate(merged):
        c["id"] = f"ezis_{i}"

    return merged


# ---------------------------------------------------------------------------
# Step 3: QA generation
# ---------------------------------------------------------------------------

_HARD_QUESTION_PROMPT = """\
다음은 EZIS Oracle Database 모니터링 솔루션의 사용자 매뉴얼 일부입니다.

이 텍스트를 읽지 않으면 답하기 어려운 질문을 {n}개 생성하세요.

규칙:
- 단순 키워드 검색으로 바로 답할 수 없는 질문 (절차, 조건, 비교, 원인, 다중 단계 필요)
- 해당 텍스트에 정답이 명확히 있어야 함
- 실제 사용자가 매뉴얼을 검색할 때 입력할 법한 자연스러운 한국어 질문
- 정답은 1~3문장으로 요약 가능해야 함

텍스트:
{chunk_text}

반드시 아래 JSON 배열 형식으로만 응답하세요 (다른 텍스트 없이):
[
  {{"question": "질문 내용", "answer_summary": "핵심 정답 요약"}},
  ...
]"""


def _generate_questions_for_chunk(
    client: "anthropic.Anthropic",
    chunk: dict,
    n: int = 2,
    model: str = "claude-haiku-4-5-20251001",
    retries: int = 3,
) -> list[dict]:
    """Call Claude API to generate hard questions for a single chunk."""
    prompt = _HARD_QUESTION_PROMPT.format(
        n=n,
        chunk_text=chunk["text"][:3000],  # cap to avoid token limits
    )
    for attempt in range(retries):
        try:
            msg = client.messages.create(
                model=model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = msg.content[0].text.strip()
            # Extract JSON array (may be wrapped in markdown)
            json_match = re.search(r"\[.*\]", raw, re.DOTALL)
            if not json_match:
                continue
            items = json.loads(json_match.group())
            return [
                {
                    "question": item["question"],
                    "answer_summary": item.get("answer_summary", ""),
                    "chunk_id": chunk["id"],
                }
                for item in items
                if "question" in item
            ]
        except (json.JSONDecodeError, KeyError, Exception):
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return []


def _generate_questions_mock(chunk: dict, n: int = 2) -> list[dict]:
    """Template-based fallback when API is unavailable."""
    templates = [
        "이 섹션에서 설명하는 주요 기능은 무엇인가?",
        "이 설정을 적용하려면 어떤 단계를 거쳐야 하는가?",
        "이 항목의 제한 사항 또는 주의 사항은 무엇인가?",
        "이 기능을 사용하기 위한 사전 조건은 무엇인가?",
    ]
    results = []
    for i in range(min(n, len(templates))):
        section = chunk.get("section", "?")
        results.append({
            "question": f"[섹션 {section}] {templates[i]}",
            "answer_summary": "(mock — 실제 QA 생성 시 크레딧 충전 필요)",
            "chunk_id": chunk["id"],
        })
    return results


def generate_qa_set(
    chunks: list[dict],
    n_per_chunk: int = 2,
    model: str = "claude-haiku-4-5-20251001",
    max_chunks: int | None = None,
    mock: bool = False,
) -> list[dict]:
    """Generate QA pairs for all chunks, return MIRACL-compatible query list."""
    if not mock and anthropic is None:
        raise ImportError("anthropic required: uv pip install anthropic")

    client = None if mock else anthropic.Anthropic()
    queries: list[dict] = []
    target_chunks = chunks[:max_chunks] if max_chunks else chunks

    for i, chunk in enumerate(target_chunks):
        print(f"  [{i+1}/{len(target_chunks)}] {'[mock] ' if mock else ''}Generating questions "
              f"for {chunk['id']} (section={chunk['section']}, {len(chunk['text'])} chars)...")
        if mock:
            qa_pairs = _generate_questions_mock(chunk, n=n_per_chunk)
        else:
            qa_pairs = _generate_questions_for_chunk(client, chunk, n=n_per_chunk, model=model)
        for qa in qa_pairs:
            queries.append({
                "query_id": f"ezis_q_{len(queries)}",
                "text": qa["question"],
                "relevant_ids": [chunk["id"]],
                "answer_summary": qa["answer_summary"],
            })
        if not mock:
            time.sleep(0.3)

    return queries


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 0-B: EZIS PDF → QA set")
    parser.add_argument("--pdf", required=True, help="Path to EZIS_Oracle_Manual.pdf")
    parser.add_argument("--output-dir", default="data/ezis")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse and chunk only — skip LLM calls")
    parser.add_argument("--max-chunks", type=int, default=None,
                        help="Limit QA generation to first N chunks")
    parser.add_argument("--n-per-chunk", type=int, default=2,
                        help="Questions per chunk (default: 2)")
    parser.add_argument("--model", default="claude-haiku-4-5-20251001",
                        help="Claude model for QA generation")
    parser.add_argument("--min-chars", type=int, default=200)
    parser.add_argument("--max-chars", type=int, default=2000)
    parser.add_argument("--mock", action="store_true",
                        help="Use template questions instead of Claude API (no credits needed)")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    # Step 1: Parse
    print(f"[Step 1] Parsing PDF: {args.pdf}")
    pages = parse_pdf(args.pdf)
    print(f"  → {len(pages)} pages extracted")

    # Step 2: Chunk
    print("[Step 2] Chunking by section...")
    chunks = chunk_by_section(
        pages,
        min_chars=args.min_chars,
        max_chars=args.max_chars,
    )
    print(f"  → {len(chunks)} chunks created")
    for c in chunks[:5]:
        print(f"     {c['id']} section={c['section']} pages={c['page_start']}-{c['page_end']} "
              f"len={len(c['text'])}")

    # Save chunks
    chunks_path = os.path.join(args.output_dir, "chunks.json")
    with open(chunks_path, "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False, indent=2)
    print(f"  → saved: {chunks_path}")

    if args.dry_run:
        print("[dry-run] Skipping QA generation.")
        return

    # Step 3: Generate QA
    print(f"[Step 3] Generating QA (model={args.model}, n_per_chunk={args.n_per_chunk}, mock={args.mock})...")
    queries = generate_qa_set(
        chunks,
        n_per_chunk=args.n_per_chunk,
        model=args.model,
        max_chunks=args.max_chunks,
        mock=args.mock,
    )
    print(f"  → {len(queries)} queries generated")

    # Save queries (MIRACL format)
    queries_path = os.path.join(args.output_dir, "queries.json")
    with open(queries_path, "w", encoding="utf-8") as f:
        json.dump(queries, f, ensure_ascii=False, indent=2)
    print(f"  → saved: {queries_path}")

    # Summary
    print("\n[Summary]")
    print(f"  Chunks : {len(chunks)}")
    print(f"  Queries: {len(queries)}")
    print(f"  Output : {args.output_dir}/")


if __name__ == "__main__":
    main()
