# Phase 6-1: VectorChord-BM25 + textsearch_ko Full Evaluation

**Generated:** 2026-03-25 15:24:08
**Vocab size:** 48915 terms

---

## MIRACL-ko Results (10K corpus, 213 queries)

| Metric | Value |
|--------|-------|
| NDCG@10 | **0.5888** |
| Recall@10 | 0.7236 |
| MRR | 0.6034 |
| Latency p50 | 0.86 ms |
| Latency p95 | 1.39 ms |
| Queries evaluated | 213 / 213 |

---

## EZIS Results (97 docs, 131 queries)

| Metric | Value |
|--------|-------|
| NDCG@10 | **0.9024** |
| Recall@10 | 0.9847 |
| MRR | 0.8758 |
| Latency p50 | 0.51 ms |
| Latency p95 | 0.68 ms |
| Queries evaluated | 131 / 131 |

---

## Phase Comparison — same tokenizer (textsearch_ko / MeCab)

### MIRACL-ko

| Phase | Method | NDCG@10 | delta vs P6 | p50 latency |
|-------|--------|---------|-------------|-------------|
| 2 | pl/pgsql BM25 + MeCab | 0.6412 | +0.0524 | 10.44ms |
| 3 | pgvector-sparse BM25 (MeCab) | 0.5323 | -0.0565 | 18.05ms |
| **6** | **VectorChord-BM25 + textsearch_ko** | **0.5888** | — | **0.86ms** |

### EZIS

| Phase | Method | NDCG@10 | delta vs P6 |
|-------|--------|---------|-------------|
| 2 | pl/pgsql BM25 + MeCab | 0.9290 | +0.0266 |
| 3 | pgvector-sparse BM25 (MeCab) | 0.9124 | +0.0100 |
| **6** | **VectorChord-BM25 + textsearch_ko** | **0.9024** | — |

**Root cause of Phase 6 gap vs Phase 2:** `tsvector_to_array` returns unique lexemes only
(TF=1 always). Phase 2 pl/pgsql used `mecabko_analyze()` for actual term frequencies.
Fix for Phase 6-2: replace `tsvector_to_array` with `mecabko_analyze()` in vectorizer.

---

## Architecture

```
textsearch_ko (MeCab, main DB port 5432)
    -> tsvector_to_array() -> Python vocab -> {id:count}::bm25vector
VectorChord-BM25 (vchord-suite, port 5436)
    CREATE INDEX <table>_emb_idx USING bm25 (emb bm25_ops)
    SELECT id ORDER BY emb <&> to_bm25query('<table>_emb_idx', q::bm25vector)
```

