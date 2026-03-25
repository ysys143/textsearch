"""
Phase 6-0: VectorChord-BM25 + textsearch_ko 타당성 조사

테스트 흐름:
1. 연결 경로 B 검증: tsvector_to_array() → bm25vector 직접 구성
2. 인덱스 생성 + 쿼리 동작 확인
3. MIRACL 1000건 서브셋으로 NDCG@10 빠른 측정
4. Go/No-Go 판정 리포트 생성
"""

import argparse
import json
import math
import os
import sys
import time
from collections import Counter
from datetime import datetime
from typing import Dict, List, Optional, Tuple

try:
    import psycopg2
except ImportError:
    print("[ERROR] psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Metric helpers (inline — no external dep needed)
# ---------------------------------------------------------------------------

def ndcg_at_k(relevant_ids: List[str], retrieved_ids: List[str], k: int = 10) -> float:
    """Compute NDCG@k given a list of relevant doc IDs and retrieved doc IDs."""
    relevant_set = set(relevant_ids)
    dcg = 0.0
    for rank, doc_id in enumerate(retrieved_ids[:k], start=1):
        if doc_id in relevant_set:
            dcg += 1.0 / math.log2(rank + 1)
    ideal_hits = min(len(relevant_set), k)
    idcg = sum(1.0 / math.log2(r + 1) for r in range(1, ideal_hits + 1))
    return dcg / idcg if idcg > 0 else 0.0


def recall_at_k(relevant_ids: List[str], retrieved_ids: List[str], k: int = 10) -> float:
    """Compute Recall@k."""
    relevant_set = set(relevant_ids)
    hits = sum(1 for doc_id in retrieved_ids[:k] if doc_id in relevant_set)
    return hits / len(relevant_set) if relevant_set else 0.0


# ---------------------------------------------------------------------------
# Vocab building + bm25vector construction
# ---------------------------------------------------------------------------

def build_vocab_from_corpus(
    conn_main, texts: List[str], max_vocab: int = 50000
) -> Dict[str, int]:
    """Build term->id mapping using textsearch_ko on main DB."""
    term_counts: Counter = Counter()
    with conn_main.cursor() as cur:
        for text in texts:
            if not text or not text.strip():
                continue
            try:
                cur.execute(
                    "SELECT tsvector_to_array(to_tsvector('public.korean', %s))",
                    (text,),
                )
                row = cur.fetchone()
                terms = row[0] if row and row[0] else []
                term_counts.update(terms)
            except Exception as e:
                conn_main.rollback()
                print(f"  [WARN] tokenize error: {e}")
    vocab = {
        term: idx + 1
        for idx, (term, _) in enumerate(term_counts.most_common(max_vocab))
    }
    return vocab


def text_to_bm25vector(conn_main, text: str, vocab: Dict[str, int]) -> Optional[str]:
    """Convert text to bm25vector string using textsearch_ko."""
    if not text or not text.strip():
        return None
    with conn_main.cursor() as cur:
        cur.execute(
            "SELECT tsvector_to_array(to_tsvector('public.korean', %s))",
            (text,),
        )
        row = cur.fetchone()
        terms = row[0] if row and row[0] else []
    counts = Counter(t for t in terms if t in vocab)
    if not counts:
        return None
    vec_str = ",".join(f"{vocab[t]}:{c}" for t, c in counts.items())
    return f"{{{vec_str}}}"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

DATA_DIR = "/Users/jaesolshin/Documents/GitHub/textsearch/data/miracl"


def load_docs(path: str = os.path.join(DATA_DIR, "docs_ko_miracl.json")) -> List[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_queries(path: str = os.path.join(DATA_DIR, "queries_dev.json")) -> List[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def connect(db_url: str, label: str = "DB"):
    try:
        conn = psycopg2.connect(db_url)
        conn.autocommit = False
        print(f"  [OK] Connected to {label}: {db_url}")
        return conn
    except Exception as e:
        print(f"  [FAIL] Cannot connect to {label} ({db_url}): {e}")
        return None


# ---------------------------------------------------------------------------
# Test 0: Extension availability
# ---------------------------------------------------------------------------

def test0_extensions(conn_phase6, conn_main) -> dict:
    print("\n=== Test 0: Extension availability ===")
    result = {"vchord_bm25": False, "textsearch_ko_main": False, "textsearch_ko_phase6": False}

    # Check vchord_bm25 on phase6 DB
    if conn_phase6:
        try:
            with conn_phase6.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS pg_tokenizer CASCADE;")
                cur.execute("CREATE EXTENSION IF NOT EXISTS vchord_bm25 CASCADE;")
                conn_phase6.commit()
                cur.execute("SELECT extname FROM pg_extension WHERE extname = 'vchord_bm25';")
                row = cur.fetchone()
                result["vchord_bm25"] = row is not None
                print(f"  vchord_bm25 extension: {'OK' if result['vchord_bm25'] else 'NOT FOUND'}")
        except Exception as e:
            conn_phase6.rollback()
            print(f"  [FAIL] vchord_bm25 extension error: {e}")

    # Check textsearch_ko on main DB
    if conn_main:
        try:
            with conn_main.cursor() as cur:
                cur.execute(
                    "SELECT tsvector_to_array(to_tsvector('public.korean', '한국어 테스트'))"
                )
                row = cur.fetchone()
                result["textsearch_ko_main"] = row is not None and len(row[0]) > 0
                print(f"  textsearch_ko (main DB): {'OK' if result['textsearch_ko_main'] else 'FAIL'}")
                if result["textsearch_ko_main"]:
                    print(f"    sample tokens: {row[0]}")
        except Exception as e:
            conn_main.rollback()
            print(f"  [FAIL] textsearch_ko on main DB: {e}")

    # Check textsearch_ko on phase6 DB (may not be installed)
    if conn_phase6:
        try:
            with conn_phase6.cursor() as cur:
                cur.execute(
                    "SELECT tsvector_to_array(to_tsvector('public.korean', '테스트'))"
                )
                row = cur.fetchone()
                result["textsearch_ko_phase6"] = row is not None
                print(f"  textsearch_ko (phase6 DB): OK")
        except Exception as e:
            conn_phase6.rollback()
            print(f"  textsearch_ko (phase6 DB): NOT available (will use main DB bridge)")
            result["textsearch_ko_phase6"] = False

    return result


# ---------------------------------------------------------------------------
# Test 1: bm25vector construction via bridge
# ---------------------------------------------------------------------------

def test1_bm25vector(conn_phase6, conn_main) -> dict:
    print("\n=== Test 1: bm25vector construction (textsearch_ko bridge) ===")
    result = {"pass": False, "details": ""}

    if not conn_phase6 or not conn_main:
        result["details"] = "Connection unavailable"
        print(f"  SKIP: {result['details']}")
        return result

    try:
        # Tokenize via main DB
        with conn_main.cursor() as cur:
            cur.execute(
                "SELECT tsvector_to_array(to_tsvector('public.korean', '한국어 검색 엔진 성능 비교'))"
            )
            terms = cur.fetchone()[0] or []
        print(f"  textsearch_ko tokens: {terms}")

        if not terms:
            result["details"] = "No tokens returned from textsearch_ko"
            print(f"  FAIL: {result['details']}")
            return result

        # Build simple vocab + bm25vector literal
        vocab = {term: idx + 1 for idx, term in enumerate(terms)}
        counts = Counter(terms)
        vec_str = ",".join(f"{vocab[t]}:{c}" for t, c in counts.items())
        bm25vec_literal = f"{{{vec_str}}}"
        print(f"  bm25vector literal: '{bm25vec_literal}'")

        # Test insert + query on phase6 DB
        with conn_phase6.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS t6_probe;")
            cur.execute("CREATE TEMP TABLE t6_probe (id SERIAL, emb bm25vector);")
            cur.execute(
                f"INSERT INTO t6_probe (emb) VALUES (%s::bm25vector)",
                (bm25vec_literal,),
            )
            cur.execute("SELECT id, emb::text FROM t6_probe;")
            row = cur.fetchone()
            conn_phase6.commit()

        print(f"  Inserted and retrieved: id={row[0]}, emb={row[1]}")
        result["pass"] = True
        result["details"] = f"bm25vector round-trip OK. tokens={terms}"
    except Exception as e:
        conn_phase6.rollback()
        result["details"] = str(e)
        print(f"  FAIL: {e}")

    print(f"  Result: {'PASS' if result['pass'] else 'FAIL'}")
    return result


# ---------------------------------------------------------------------------
# Test 2: Index + search
# ---------------------------------------------------------------------------

def test2_index_search(conn_phase6, conn_main, sample_docs: List[dict]) -> dict:
    print("\n=== Test 2: BM25 index creation + search ===")
    result = {"pass": False, "details": "", "results_returned": 0}

    if not conn_phase6 or not conn_main:
        result["details"] = "Connection unavailable"
        print(f"  SKIP: {result['details']}")
        return result

    try:
        # Use first 100 docs
        docs_100 = sample_docs[:100]
        texts = [d["text"] for d in docs_100]

        print(f"  Building vocab from {len(texts)} docs via textsearch_ko...")
        vocab = build_vocab_from_corpus(conn_main, texts)
        print(f"  Vocab size: {len(vocab)}")

        if not vocab:
            result["details"] = "Empty vocab — textsearch_ko produced no tokens"
            print(f"  FAIL: {result['details']}")
            return result

        # Create temp table and insert
        with conn_phase6.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS t6_test;")
            cur.execute(
                "CREATE TEMP TABLE t6_test (id TEXT, passage TEXT, emb bm25vector);"
            )

        inserted = 0
        skipped = 0
        for doc in docs_100:
            vec = text_to_bm25vector(conn_main, doc["text"], vocab)
            if vec is None:
                skipped += 1
                continue
            with conn_phase6.cursor() as cur:
                cur.execute(
                    "INSERT INTO t6_test (id, passage, emb) VALUES (%s, %s, %s::bm25vector)",
                    (doc["id"], doc["text"][:500], vec),
                )
            inserted += 1

        conn_phase6.commit()
        print(f"  Inserted {inserted} docs ({skipped} skipped — no tokens)")

        if inserted == 0:
            result["details"] = "No docs inserted"
            print(f"  FAIL: {result['details']}")
            return result

        # Build BM25 index
        print("  Creating bm25 index...")
        with conn_phase6.cursor() as cur:
            cur.execute("CREATE INDEX ON t6_test USING bm25 (emb bm25_ops);")
        conn_phase6.commit()
        print("  Index created.")

        # Run 5 test queries
        test_queries = [
            "합성생물학 연구",
            "한국 역사 문화",
            "인공지능 머신러닝",
            "환경 기후 변화",
            "경제 성장 발전",
        ]
        total_results = 0
        for q_text in test_queries:
            q_vec = text_to_bm25vector(conn_main, q_text, vocab)
            if q_vec is None:
                print(f"  Query '{q_text}': no tokens in vocab, skipping")
                continue
            with conn_phase6.cursor() as cur:
                cur.execute(
                    "SELECT id FROM t6_test ORDER BY emb <&> %s::bm25query LIMIT 5",
                    (q_vec,),
                )
                rows = cur.fetchall()
            total_results += len(rows)
            print(f"  Query '{q_text}': {len(rows)} results returned")

        result["results_returned"] = total_results
        result["pass"] = total_results > 0
        result["details"] = f"inserted={inserted}, total_results={total_results}"
    except Exception as e:
        try:
            conn_phase6.rollback()
        except Exception:
            pass
        result["details"] = str(e)
        print(f"  FAIL: {e}")

    print(f"  Result: {'PASS' if result['pass'] else 'FAIL'}")
    return result


# ---------------------------------------------------------------------------
# Test 3: Quick NDCG on MIRACL subset
# ---------------------------------------------------------------------------

def test3_ndcg(
    conn_phase6,
    conn_main,
    all_docs: List[dict],
    queries: List[dict],
    sample_size: int,
) -> dict:
    print(f"\n=== Test 3: NDCG@10 on MIRACL subset (n={sample_size}) ===")
    result = {
        "pass": False,
        "ndcg_at_10": 0.0,
        "recall_at_10": 0.0,
        "latency_p50_ms": 0.0,
        "queries_evaluated": 0,
        "details": "",
    }
    NDCG_THRESHOLD = 0.55

    if not conn_phase6 or not conn_main:
        result["details"] = "Connection unavailable"
        print(f"  SKIP: {result['details']}")
        return result

    try:
        docs = all_docs[:sample_size]
        texts = [d["text"] for d in docs]

        print(f"  Building vocab from {len(texts)} docs...")
        vocab = build_vocab_from_corpus(conn_main, texts)
        print(f"  Vocab size: {len(vocab)}")

        if not vocab:
            result["details"] = "Empty vocab"
            print(f"  FAIL: {result['details']}")
            return result

        # Create persistent (session) table for this test
        with conn_phase6.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS t6_ndcg_bench;")
            cur.execute(
                "CREATE TEMP TABLE t6_ndcg_bench (id TEXT, emb bm25vector);"
            )

        print(f"  Inserting {len(docs)} docs...")
        doc_id_set = set()
        inserted = 0
        batch = []
        for doc in docs:
            vec = text_to_bm25vector(conn_main, doc["text"], vocab)
            if vec is None:
                continue
            batch.append((doc["id"], vec))
            doc_id_set.add(doc["id"])
            if len(batch) >= 200:
                with conn_phase6.cursor() as cur:
                    for did, v in batch:
                        cur.execute(
                            "INSERT INTO t6_ndcg_bench (id, emb) VALUES (%s, %s::bm25vector)",
                            (did, v),
                        )
                conn_phase6.commit()
                inserted += len(batch)
                batch = []

        if batch:
            with conn_phase6.cursor() as cur:
                for did, v in batch:
                    cur.execute(
                        "INSERT INTO t6_ndcg_bench (id, emb) VALUES (%s, %s::bm25vector)",
                        (did, v),
                    )
            conn_phase6.commit()
            inserted += len(batch)

        print(f"  Inserted {inserted} docs.")

        if inserted == 0:
            result["details"] = "No docs inserted"
            print(f"  FAIL: {result['details']}")
            return result

        # Build index
        print("  Creating bm25 index on t6_ndcg_bench...")
        with conn_phase6.cursor() as cur:
            cur.execute("CREATE INDEX ON t6_ndcg_bench USING bm25 (emb bm25_ops);")
        conn_phase6.commit()

        # Filter queries: only those with relevant docs present in our subset
        valid_queries = [
            q for q in queries
            if any(rid in doc_id_set for rid in q["relevant_ids"])
        ]
        print(f"  Valid queries (relevant docs in subset): {len(valid_queries)}/{len(queries)}")

        if not valid_queries:
            result["details"] = "No valid queries with relevant docs in subset"
            print(f"  SKIP: {result['details']}")
            return result

        ndcg_scores = []
        recall_scores = []
        latencies_ms = []

        for q in valid_queries:
            q_vec = text_to_bm25vector(conn_main, q["text"], vocab)
            if q_vec is None:
                continue

            t0 = time.perf_counter()
            with conn_phase6.cursor() as cur:
                cur.execute(
                    "SELECT id FROM t6_ndcg_bench ORDER BY emb <&> %s::bm25query LIMIT 10",
                    (q_vec,),
                )
                rows = cur.fetchall()
            elapsed_ms = (time.perf_counter() - t0) * 1000

            retrieved = [r[0] for r in rows]
            ndcg = ndcg_at_k(q["relevant_ids"], retrieved, k=10)
            rec = recall_at_k(q["relevant_ids"], retrieved, k=10)
            ndcg_scores.append(ndcg)
            recall_scores.append(rec)
            latencies_ms.append(elapsed_ms)

        if not ndcg_scores:
            result["details"] = "No queries produced results"
            print(f"  FAIL: {result['details']}")
            return result

        latencies_ms.sort()
        p50 = latencies_ms[len(latencies_ms) // 2]
        avg_ndcg = sum(ndcg_scores) / len(ndcg_scores)
        avg_recall = sum(recall_scores) / len(recall_scores)

        result["ndcg_at_10"] = avg_ndcg
        result["recall_at_10"] = avg_recall
        result["latency_p50_ms"] = p50
        result["queries_evaluated"] = len(ndcg_scores)
        result["pass"] = avg_ndcg >= NDCG_THRESHOLD
        result["details"] = (
            f"NDCG@10={avg_ndcg:.4f}, Recall@10={avg_recall:.4f}, "
            f"p50_latency={p50:.1f}ms, queries={len(ndcg_scores)}"
        )

        print(f"  NDCG@10:   {avg_ndcg:.4f} (threshold >= {NDCG_THRESHOLD})")
        print(f"  Recall@10: {avg_recall:.4f}")
        print(f"  p50 latency: {p50:.1f} ms")
        print(f"  Queries evaluated: {len(ndcg_scores)}")
        print(f"  Result: {'PASS' if result['pass'] else 'FAIL'} (threshold={NDCG_THRESHOLD})")

    except Exception as e:
        try:
            conn_phase6.rollback()
        except Exception:
            pass
        result["details"] = str(e)
        print(f"  FAIL: {e}")

    return result


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def write_report(
    output_dir: str,
    t0_result: dict,
    t1_result: dict,
    t2_result: dict,
    t3_result: Optional[dict],
    sample_size: int,
) -> str:
    os.makedirs(output_dir, exist_ok=True)
    report_path = os.path.join(output_dir, "phase6_0_feasibility_report.md")

    all_pass = (
        t1_result.get("pass", False)
        and t2_result.get("pass", False)
        and (t3_result is None or t3_result.get("pass", False))
    )
    verdict = "GO" if all_pass else "NO-GO"

    # Connection path determination
    if t0_result.get("textsearch_ko_phase6"):
        path = "A — textsearch_ko native on phase6 DB (direct)"
    elif t0_result.get("textsearch_ko_main") and t0_result.get("vchord_bm25"):
        path = "B — textsearch_ko on main DB → bm25vector on phase6 DB (bridge)"
    elif t0_result.get("vchord_bm25"):
        path = "C — vchord_bm25 only (no textsearch_ko available)"
    else:
        path = "D — Neither extension available; cannot proceed"

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "# Phase 6-0 Feasibility Report: VectorChord-BM25 + textsearch_ko",
        "",
        f"**Generated:** {now}  ",
        f"**Sample size:** {sample_size} docs  ",
        f"**Connection path:** {path}  ",
        "",
        "---",
        "",
        "## Test Results",
        "",
        "| Test | Result | Details |",
        "|------|--------|---------|",
        f"| Test 0: Extensions | {'PASS' if (t0_result.get('vchord_bm25') and t0_result.get('textsearch_ko_main')) else 'PARTIAL/FAIL'} | vchord_bm25={t0_result.get('vchord_bm25')}, textsearch_ko_main={t0_result.get('textsearch_ko_main')}, textsearch_ko_phase6={t0_result.get('textsearch_ko_phase6')} |",
        f"| Test 1: bm25vector construction | {'PASS' if t1_result.get('pass') else 'FAIL'} | {t1_result.get('details', '')} |",
        f"| Test 2: Index + search | {'PASS' if t2_result.get('pass') else 'FAIL'} | {t2_result.get('details', '')} |",
    ]

    if t3_result is not None:
        lines.append(
            f"| Test 3: NDCG@10 | {'PASS' if t3_result.get('pass') else 'FAIL'} | {t3_result.get('details', '')} |"
        )
    else:
        lines.append("| Test 3: NDCG@10 | SKIP | Test 2 did not pass |")

    lines += [
        "",
        "---",
        "",
        "## Performance Metrics",
        "",
    ]

    if t3_result and t3_result.get("queries_evaluated", 0) > 0:
        lines += [
            f"- **NDCG@10:** {t3_result['ndcg_at_10']:.4f}  ",
            f"- **Recall@10:** {t3_result['recall_at_10']:.4f}  ",
            f"- **Latency p50:** {t3_result['latency_p50_ms']:.1f} ms  ",
            f"- **Queries evaluated:** {t3_result['queries_evaluated']}  ",
            f"- **Pass threshold:** NDCG@10 ≥ 0.55  ",
        ]
    else:
        lines.append("_Metrics not available (Test 3 skipped or failed)_  ")

    lines += [
        "",
        "---",
        "",
        "## Connection Path Analysis",
        "",
        f"**Chosen path:** {path}",
        "",
        "| Path | Description | Status |",
        "|------|-------------|--------|",
        f"| A | textsearch_ko native on phase6 DB | {'Available' if t0_result.get('textsearch_ko_phase6') else 'Not available'} |",
        f"| B | textsearch_ko bridge (main DB) → bm25vector (phase6 DB) | {'Available' if (t0_result.get('textsearch_ko_main') and t0_result.get('vchord_bm25')) else 'Not available'} |",
        f"| C | vchord_bm25 only (no Korean tokenizer) | {'Available' if t0_result.get('vchord_bm25') else 'Not available'} |",
        f"| D | No viable path | {'Active' if not t0_result.get('vchord_bm25') else 'Not active'} |",
        "",
        "---",
        "",
        "## Go/No-Go Conclusion",
        "",
        f"## {verdict}",
        "",
    ]

    if verdict == "GO":
        lines += [
            "VectorChord-BM25 with textsearch_ko bridge is feasible.",
            "",
            "- bm25vector construction via `tsvector_to_array()` bridge works correctly",
            "- BM25 index creation and similarity search operational",
            f"- NDCG@10 = {t3_result['ndcg_at_10']:.4f} meets threshold ≥ 0.55" if t3_result else "",
            "",
            "**Recommendation:** Proceed to Phase 6-1 (full corpus evaluation).",
        ]
    else:
        failures = []
        if not t1_result.get("pass"):
            failures.append(f"Test 1 failed: {t1_result.get('details')}")
        if not t2_result.get("pass"):
            failures.append(f"Test 2 failed: {t2_result.get('details')}")
        if t3_result and not t3_result.get("pass"):
            failures.append(
                f"Test 3 failed: NDCG@10={t3_result.get('ndcg_at_10', 0):.4f} < 0.55"
            )
        if not t0_result.get("vchord_bm25"):
            failures.append("vchord_bm25 extension not available on phase6 DB")

        lines += [
            "Feasibility test did not pass. Blocking issues:",
            "",
        ]
        for f in failures:
            lines.append(f"- {f}")
        lines += [
            "",
            "**Recommendation:** Resolve blocking issues before proceeding.",
            "Check that the phase6 container is running (`docker compose --profile phase6 up -d`)",
            "and that vchord_bm25 extension is installable.",
        ]

    content = "\n".join(lines) + "\n"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"\n  Report written: {report_path}")
    return report_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Phase 6-0: VectorChord-BM25 + textsearch_ko feasibility test"
    )
    parser.add_argument(
        "--db-url",
        default="postgresql://postgres:postgres@localhost:5436/dev",
        help="Phase6 DB (VectorChord-BM25) connection URL",
    )
    parser.add_argument(
        "--main-db-url",
        default="postgresql://postgres:postgres@localhost:5432/dev",
        help="Main DB (textsearch_ko) connection URL",
    )
    parser.add_argument(
        "--output-dir",
        default="results/phase6",
        help="Directory for report output",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=1000,
        help="Number of MIRACL docs to use for NDCG test",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Phase 6-0: VectorChord-BM25 + textsearch_ko Feasibility Test")
    print("=" * 60)
    print(f"  Phase6 DB:  {args.db_url}")
    print(f"  Main DB:    {args.main_db_url}")
    print(f"  Sample size: {args.sample_size}")
    print(f"  Output dir:  {args.output_dir}")

    # Connect
    conn_phase6 = connect(args.db_url, "phase6 DB (VectorChord-BM25)")
    conn_main = connect(args.main_db_url, "main DB (textsearch_ko)")

    if conn_phase6 is None:
        print(
            "\n[ERROR] Cannot connect to phase6 DB. Is the container running?\n"
            "  docker compose --profile phase6 up -d"
        )

    if conn_main is None:
        print("\n[ERROR] Cannot connect to main DB (textsearch_ko).")

    # Load MIRACL data
    print("\nLoading MIRACL data...")
    try:
        all_docs = load_docs()
        queries = load_queries()
        print(f"  Docs: {len(all_docs)}, Queries: {len(queries)}")
    except Exception as e:
        print(f"  [ERROR] Failed to load MIRACL data: {e}")
        all_docs = []
        queries = []

    # Run tests
    t0 = test0_extensions(conn_phase6, conn_main)
    t1 = test1_bm25vector(conn_phase6, conn_main)
    t2 = test2_index_search(conn_phase6, conn_main, all_docs) if all_docs else {"pass": False, "details": "No docs loaded"}
    t3 = None
    if t2.get("pass") and all_docs and queries:
        t3 = test3_ndcg(conn_phase6, conn_main, all_docs, queries, args.sample_size)
    else:
        print("\n=== Test 3: NDCG@10 — SKIPPED (Test 2 did not pass or no data) ===")

    # Write report
    report_path = write_report(
        args.output_dir, t0, t1, t2, t3, args.sample_size
    )

    # Close connections
    for conn, label in [(conn_phase6, "phase6"), (conn_main, "main")]:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Test 0 (extensions):   {'PASS' if (t0.get('vchord_bm25') and t0.get('textsearch_ko_main')) else 'PARTIAL/FAIL'}")
    print(f"  Test 1 (bm25vector):   {'PASS' if t1.get('pass') else 'FAIL'}")
    print(f"  Test 2 (index+search): {'PASS' if t2.get('pass') else 'FAIL'}")
    if t3:
        print(f"  Test 3 (NDCG@10):      {'PASS' if t3.get('pass') else 'FAIL'} — {t3.get('ndcg_at_10', 0):.4f}")
    else:
        print("  Test 3 (NDCG@10):      SKIP")
    all_pass = t1.get("pass") and t2.get("pass") and (t3 is None or t3.get("pass"))
    print(f"\n  VERDICT: {'GO' if all_pass else 'NO-GO'}")
    print(f"  Report:  {report_path}")


if __name__ == "__main__":
    main()
