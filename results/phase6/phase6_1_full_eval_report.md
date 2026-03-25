# Phase 6-1: VectorChord-BM25 + textsearch_ko Full Evaluation

**Generated:** 2026-03-25 15:15:27
**Vocab size:** 48915 terms

---

## MIRACL-ko Results (10K corpus, 213 queries)

| Metric | Value |
|--------|-------|
| NDCG@10 | **0.5888** |
| Recall@10 | 0.7236 |
| MRR | 0.6034 |
| Latency p50 | 0.92 ms |
| Latency p95 | 2.00 ms |
| Queries evaluated | 213 / 213 |

---

## EZIS Results (97 docs, 131 queries)

| Metric | Value |
|--------|-------|
| NDCG@10 | **0.9024** |
| Recall@10 | 0.9847 |
| MRR | 0.8758 |
| Latency p50 | 0.70 ms |
| Latency p95 | 0.97 ms |
| Queries evaluated | 131 / 131 |

---

## Phase Comparison (MIRACL-ko)

| Phase | Method | NDCG@10 | p50 latency |
|-------|--------|---------|-------------|
| 2 | pg_textsearch + MeCab (BM25/WAND) | 0.3374 | - |
| 3 | pgvector-sparse BM25 (kiwi-cong) | 0.6326 | 4.24ms |
| 4 | BGE-M3 dense | 0.7915 | - |
| 5 | pl/pgsql BM25 v2 (best) | - | 0.73ms |
| **6** | **VectorChord-BM25 + textsearch_ko** | **0.5888** | **0.92ms** |

---

## Architecture

```
textsearch_ko (MeCab, main DB port 5432)
    -> tsvector_to_array() -> Python vocab -> {id:count}::bm25vector
VectorChord-BM25 (vchord-suite, port 5436)
    CREATE INDEX <table>_emb_idx USING bm25 (emb bm25_ops)
    SELECT id ORDER BY emb <&> to_bm25query('<table>_emb_idx', q::bm25vector)
```

