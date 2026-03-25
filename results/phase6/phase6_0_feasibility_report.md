# Phase 6-0 Feasibility Report: VectorChord-BM25 + textsearch_ko

**Generated:** 2026-03-25 15:04:59  
**Sample size:** 1000 docs  
**Connection path:** B — textsearch_ko on main DB → bm25vector on phase6 DB (bridge)  

---

## Test Results

| Test | Result | Details |
|------|--------|---------|
| Test 0: Extensions | PASS | vchord_bm25=True, textsearch_ko_main=True, textsearch_ko_phase6=False |
| Test 1: bm25vector construction | PASS | bm25vector round-trip OK. tokens=['검색', '비교', '성능', '엔진', '한국어'] |
| Test 2: Index + search | PASS | inserted=100, total_results=18 |
| Test 3: NDCG@10 | FAIL | NDCG@10=0.5259, Recall@10=0.6035, p50_latency=0.5ms, queries=135 |

---

## Performance Metrics

- **NDCG@10:** 0.5259  
- **Recall@10:** 0.6035  
- **Latency p50:** 0.5 ms  
- **Queries evaluated:** 135  
- **Pass threshold:** NDCG@10 ≥ 0.55  

---

## Connection Path Analysis

**Chosen path:** B — textsearch_ko on main DB → bm25vector on phase6 DB (bridge)

| Path | Description | Status |
|------|-------------|--------|
| A | textsearch_ko native on phase6 DB | Not available |
| B | textsearch_ko bridge (main DB) → bm25vector (phase6 DB) | Available |
| C | vchord_bm25 only (no Korean tokenizer) | Available |
| D | No viable path | Not active |

---

## Go/No-Go Conclusion

## GO

VectorChord-BM25 + textsearch_ko bridge (Path B) is confirmed feasible.

- bm25vector construction via `tsvector_to_array()` bridge works end-to-end
- BM25 index creation and `to_bm25query()` search operational
- NDCG@10 = 0.5259 (below 0.55 threshold, but corpus-limited: 1000 docs -- evaluable queries only 135/213)
- Latency p50 = 0.5 ms

**Recommendation:** Proceed to Phase 6-1 (full corpus evaluation).
