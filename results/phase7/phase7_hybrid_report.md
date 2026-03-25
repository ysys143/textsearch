# Phase 7 Section 2: Hybrid Search Benchmark

**Generated:** 2026-03-25 20:03:53
**Corpus:** p7_hybrid_miracl (10K docs, BGE-M3 1024-dim)
**Queries:** MIRACL-ko dev 213

---

## Quality (NDCG@10 / Recall@10 / MRR)

| Method | NDCG@10 | Recall@10 | MRR |
|--------|---------|-----------|-----|
| BM25 AND | 0.6385 | 0.7974 | 0.6172 |
| Dense | 0.7904 | 0.913 | 0.801 |
| RRF | 0.7661 | 0.8875 | 0.7813 |
| BM25 OR | 0.271 | 0.4169 | 0.2751 |

## Latency (ms)

| Method | p50 | p95 | p99 |
|--------|-----|-----|-----|
| BM25 AND | 0.51ms | 0.97ms | 2.04ms |
| Dense | 1.47ms | 2.05ms | 2.43ms |
| RRF | 2.07ms | 2.81ms | 3.12ms |
| BM25 OR | 3.23ms | 24.75ms | 47.11ms |

---

## 비고

- **BM25 AND**: pg_textsearch `<@>` 연산자 (AND matching)
- **Dense**: HNSW cosine (pgvector), 쿼리 임베딩 pre-computed
- **RRF**: BM25_AND(top-60) + Dense(top-60), k=60
- **BM25 OR**: GIN + ts_rank_cd (OR matching)
