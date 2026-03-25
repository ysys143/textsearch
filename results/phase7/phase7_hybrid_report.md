# Phase 7 Section 2: Hybrid Search Benchmark

**Generated:** 2026-03-25 20:10:15
**Infrastructure:** MeCab (textsearch_ko) + pg_textsearch BM25 + pgvector HNSW
**Fusion:** RRF k=60, Bayesian α=0.5 (BM25) / 0.5 (Dense)

---

## MIRACL — p7_hybrid_miracl (10K)

### Quality

| Method | NDCG@10 | Recall@10 | MRR |
|--------|---------|-----------|-----|
| BM25 | 0.6385 | 0.7974 | 0.6172 |
| Dense | 0.7904 | 0.913 | 0.801 |
| RRF | 0.7661 | 0.8875 | 0.7813 |
| Bayesian | 0.7272 | 0.8706 | 0.7218 |

### Latency

| Method | p50 | p95 | p99 |
|--------|-----|-----|-----|
| BM25 | 0.5ms | 0.7ms | 0.93ms |
| Dense | 1.33ms | 1.71ms | 1.82ms |
| RRF | 2.19ms | 3.13ms | 3.8ms |
| Bayesian | 9.51ms | 12.5ms | 14.37ms |

## EZIS — p7_hybrid_ezis (97)

### Quality

| Method | NDCG@10 | Recall@10 | MRR |
|--------|---------|-----------|-----|
| BM25 | 0.9162 | 0.9847 | 0.8936 |
| Dense | 0.8041 | 0.9351 | 0.7624 |
| RRF | 0.8867 | 0.9695 | 0.8595 |
| Bayesian | 0.9249 | 0.9847 | 0.9033 |

### Latency

| Method | p50 | p95 | p99 |
|--------|-----|-----|-----|
| BM25 | 0.48ms | 0.73ms | 1.14ms |
| Dense | 0.67ms | 0.83ms | 0.95ms |
| RRF | 1.27ms | 1.48ms | 1.57ms |
| Bayesian | 14.18ms | 15.83ms | 17.01ms |

---

## 비고

- **BM25**: pg_textsearch `<@>` AND matching, MeCab 토크나이저
- **Dense**: pgvector HNSW cosine (BGE-M3 1024-dim)
- **RRF**: 각 컴포넌트 top-60 후 `1/(k+rank)` 합산 (k=60)
- **Bayesian**: `to_bm25query` 실제 BM25 스코어 + cosine sim 정규화 후 α=0.5:0.5 결합
