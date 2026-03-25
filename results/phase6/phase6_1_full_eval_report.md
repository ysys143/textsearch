# Phase 6-1: VectorChord-BM25 + textsearch_ko Full Evaluation (TF=1 (tsvector_to_array unique lexemes))

**Generated:** 2026-03-25 16:49:25
**Vocab size:** 48915 terms

---

## MIRACL-ko Results (10K corpus, 213 queries)

| Metric | Value |
|--------|-------|
| NDCG@10 | **0.5888** |
| Recall@10 | 0.7236 |
| MRR | 0.6034 |
| Latency p50 | 1.01 ms |
| Latency p95 | 2.12 ms |
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
| 5T | pg_textsearch AND (<@>) | 0.6401 | +0.0513 | 0.5ms |
| 5B v2 | pl/pgsql BM25 v2 + MeCab | 0.6414 | +0.0526 | 11.3ms |
| **6-1** | **VectorChord-BM25 + textsearch_ko** | **0.5888** | — | **1.01ms** |

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

