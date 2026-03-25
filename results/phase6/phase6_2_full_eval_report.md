# Phase 6-2: VectorChord-BM25 + textsearch_ko Full Evaluation (real-TF (positions from unnest(tsvector)))

**Generated:** 2026-03-25 16:49:34
**Vocab size:** 48915 terms

---

## MIRACL-ko Results (10K corpus, 213 queries)

| Metric | Value |
|--------|-------|
| NDCG@10 | **0.6415** |
| Recall@10 | 0.8021 |
| MRR | 0.6194 |
| Latency p50 | 0.97 ms |
| Latency p95 | 2.00 ms |
| Queries evaluated | 213 / 213 |

---

## EZIS Results (97 docs, 131 queries)

| Metric | Value |
|--------|-------|
| NDCG@10 | **0.9238** |
| Recall@10 | 0.9924 |
| MRR | 0.9013 |
| Latency p50 | 0.61 ms |
| Latency p95 | 0.81 ms |
| Queries evaluated | 131 / 131 |

---

## Phase 5 Comparison (same tokenizer: textsearch_ko / MeCab)

### MIRACL-ko

| Phase | Method | NDCG@10 | delta vs P6 | p50 latency |
|-------|--------|---------|-------------|-------------|
| 5T | pg_textsearch AND (<@>) | 0.6401 | -0.0014 | 0.5ms |
| 5B v2 | pl/pgsql BM25 v2 + MeCab | 0.6414 | -0.0001 | 11.3ms |
| **6-2** | **VectorChord-BM25 + textsearch_ko** | **0.6415** | — | **0.97ms** |

### EZIS

| Phase | Method | NDCG@10 | delta vs P6 |
|-------|--------|---------|-------------|
| 5T | pg_textsearch AND (<@>) | 0.9238 | -0.0000 |
| 5B v2 | pl/pgsql BM25 v2 + MeCab | 0.9290 | +0.0052 |
| **6-2** | **VectorChord-BM25 + textsearch_ko** | **0.9238** | — |

**TF mode:** real-TF (positions from unnest(tsvector))

---

## Architecture

```
textsearch_ko (MeCab, main DB port 5432)
    -> SELECT lexeme, array_length(positions, 1)
       FROM unnest(to_tsvector('public.korean', text))
    -> Python vocab -> {id:count}::bm25vector  (real TF)
VectorChord-BM25 (vchord-suite, port 5436)
    CREATE INDEX <table>_emb_idx USING bm25 (emb bm25_ops)
    SELECT id ORDER BY emb <&> to_bm25query('<table>_emb_idx', q::bm25vector)
```

