---
name: Korean text search benchmark run results
description: Actual benchmark execution results from Phase 1-5 run on 1000 mMARCO-ko passages
type: project
---

## Benchmark Run — 2026-03-23

**Setup**: 1000 mMARCO-ko passages, 50 synthetic queries (self-retrieval), PostgreSQL dev DB, whitespace tokenizer only

| Phase | Method | NDCG@10 | Recall@10 | MRR | Latency p50 |
|-------|--------|---------|-----------|-----|-------------|
| Phase 1 | Python BM25 / whitespace | 0.9779 | 1.0000 | 0.970 | 10.9ms |
| Phase 2 | pgvector sparse BM25 / whitespace | 0.9779 | 1.0000 | 0.970 | 10.2ms |
| Phase 3 | Whitespace analyzer screen | 1.0000 | — | — | 0.00ms (461K docs/sec) |
| Phase 4 | Neural sparse | SKIPPED — torch not installed | | | |
| Phase 5 | PostgreSQL tsvector (simple) | — | — | — | 28.3ms, 35 QPS |

**Why:** First actual execution of all benchmark scripts after implementation.

**How to apply:** Phase 1/2 produce identical quality. pgvector sparse is 6% faster. tsvector is 3× slower. Install mecab/kiwi/okt to compare Korean morphological analyzers.

**Bugs fixed during run:**
- `phase3_analyzer_screen.py` + `phase3_5_interaction_matrix.py`: `BM25Embedder_PG.embed_document()` returns pgvector `SparseVector` — use base class `BM25Embedder` for in-memory dict scoring
- `phase1_tsvector.py` + `phase2_bm25_impl.py`: tokenizer_map was missing `"whitespace": "whitespace"` entry
- `data/queries_dev.json`: original mMARCO qrel IDs (e.g. 7067032) don't match the sequential 0-999 subset loaded; rebuilt with synthetic self-retrieval queries using serial IDs
