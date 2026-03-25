# Phase 6-1: VectorChord-BM25 + textsearch_ko Full Evaluation (TF=1 (tsvector_to_array unique lexemes))

**Generated:** 2026-03-25 16:28:23
**Vocab size:** 48915 terms

---

## MIRACL-ko Results (10K corpus, 213 queries)

| Metric | Value |
|--------|-------|
| NDCG@10 | **0.5888** |
| Recall@10 | 0.7236 |
| MRR | 0.6034 |
| Latency p50 | 0.94 ms |
| Latency p95 | 2.01 ms |
| Queries evaluated | 213 / 213 |

---

## EZIS Results (97 docs, 131 queries)

| Metric | Value |
|--------|-------|
| NDCG@10 | **0.9024** |
| Recall@10 | 0.9847 |
| MRR | 0.8758 |
| Latency p50 | 0.60 ms |
| Latency p95 | 0.74 ms |
| Queries evaluated | 131 / 131 |

---

## Phase 5 Comparison (same tokenizer: textsearch_ko / MeCab)

### MIRACL-ko

| Phase | Method | NDCG@10 | delta vs P6 | p50 latency |
|-------|--------|---------|-------------|-------------|
| 5T | pg_textsearch AND (<@>) | 0.3437 | -0.2451 | 0.62ms |
| 5B v2 | pl/pgsql BM25 v2 + MeCab | 0.3491 | -0.2397 | 2.7ms |
| **6-1** | **VectorChord-BM25 + textsearch_ko** | **0.5888** | — | **0.94ms** |

### EZIS

| Phase | Method | NDCG@10 | delta vs P6 |
|-------|--------|---------|-------------|
| 5T | pg_textsearch AND (<@>) | 0.9238 | +0.0214 |
| 5B v2 | pl/pgsql BM25 v2 + MeCab | 0.9290 | +0.0266 |
| **6-1** | **VectorChord-BM25 + textsearch_ko** | **0.9024** | — |

**TF mode:** TF=1 (tsvector_to_array unique lexemes)

---

## Architecture

```
textsearch_ko (MeCab, main DB port 5432)
    -> tsvector_to_array() -> Python vocab -> {id:count}::bm25vector
VectorChord-BM25 (vchord-suite, port 5436)
    CREATE INDEX <table>_emb_idx USING bm25 (emb bm25_ops)
    SELECT id ORDER BY emb <&> to_bm25query('<table>_emb_idx', q::bm25vector)
```

