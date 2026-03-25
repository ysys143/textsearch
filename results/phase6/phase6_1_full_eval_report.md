# Phase 6-1: VectorChord-BM25 + textsearch_ko Full Evaluation

**Generated:** 2026-03-25 15:11:14
**Corpus:** 10000 docs (text_embedding, main DB)
**Vocab size:** 48915 terms
**Queries:** 213 / 213

---

## Results

| Metric | Value | vs Phase 3 MeCab BM25 |
|--------|-------|----------------------|
| NDCG@10 | **0.5888** | +0.1156 |
| Recall@10 | 0.7236 | - |
| MRR | 0.6034 | - |
| Latency p50 | 1.62 ms | +0.89ms vs Phase5 (0.73ms p50) |
| Latency p95 | 5.27 ms | - |

---

## Context: Phase Comparison

| Phase | Method | NDCG@10 | p50 latency |
|-------|--------|---------|-------------|
| 2 | pg_textsearch + MeCab (BM25/WAND) | 0.3374 | - |
| 3 | pgvector-sparse BM25 (kiwi-cong) | 0.6326 | 4.24ms |
| 4 | BGE-M3 dense | 0.7915 | - |
| 5 | pl/pgsql BM25 v2 | best method | 0.73ms |
| **6** | **VectorChord-BM25 + textsearch_ko** | **0.5888** | **1.62ms** |

---

## Architecture

```
textsearch_ko (MeCab, main DB port 5432)
    -> tsvector_to_array() -> Python vocab -> {id:count}::bm25vector
VectorChord-BM25 (vchord-suite, port 5436)
    CREATE INDEX t6_miracl_10k_emb_idx USING bm25 (emb bm25_ops)
    SELECT id ORDER BY emb <&> to_bm25query('t6_miracl_10k_emb_idx', q::bm25vector)
```

