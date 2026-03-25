# Phase 6-2: VectorChord-BM25 + textsearch_ko Full Evaluation (real-TF (positions from unnest(tsvector)))

**Generated:** 2026-03-25 15:35:45
**Vocab size:** 48915 terms

---

## MIRACL-ko Results (10K corpus, 213 queries)

| Metric | Value |
|--------|-------|
| NDCG@10 | **0.6415** |
| Recall@10 | 0.8021 |
| MRR | 0.6194 |
| Latency p50 | 0.95 ms |
| Latency p95 | 1.98 ms |
| Queries evaluated | 213 / 213 |

---

## EZIS Results (97 docs, 131 queries)

| Metric | Value |
|--------|-------|
| NDCG@10 | **0.9238** |
| Recall@10 | 0.9924 |
| MRR | 0.9013 |
| Latency p50 | 0.48 ms |
| Latency p95 | 0.62 ms |
| Queries evaluated | 131 / 131 |

---

## Phase 5 Comparison (same tokenizer: textsearch_ko / MeCab)

### MIRACL-ko

| Phase | Method | NDCG@10 | delta vs P6 | p50 latency |
|-------|--------|---------|-------------|-------------|
| 5T | pg_textsearch AND (<@>) | 0.3437 | -0.2978 | 0.62ms |
| 5B v2 | pl/pgsql BM25 v2 + MeCab | 0.3355 | -0.3060 | 3.15ms |
| **6-2** | **VectorChord-BM25 + textsearch_ko** | **0.6415** | — | **0.95ms** |

### EZIS

| Phase | Method | NDCG@10 | delta vs P6 |
|-------|--------|---------|-------------|
| 5T | pg_textsearch AND (<@>) | 0.9238 | -0.0000 |
| 5B v2 | pl/pgsql BM25 v2 + MeCab | 0.8926 | -0.0312 |
| **6-2** | **VectorChord-BM25 + textsearch_ko** | **0.9238** | — |

**TF mode:** real-TF (positions from unnest(tsvector))

---

## Architecture

```
textsearch_ko (MeCab, main DB port 5432)
    -> tsvector_to_array() -> Python vocab -> {id:count}::bm25vector
VectorChord-BM25 (vchord-suite, port 5436)
    CREATE INDEX <table>_emb_idx USING bm25 (emb bm25_ops)
    SELECT id ORDER BY emb <&> to_bm25query('<table>_emb_idx', q::bm25vector)
```

