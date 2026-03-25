# Phase 7 Section 2: Hybrid Search Benchmark

**Generated:** 2026-03-25 20:20:07
**Infrastructure:** MeCab (textsearch_ko) + pg_textsearch BM25 + pgvector HNSW
**Fusion:** RRF k=60, Bayesian α=0.5 (BM25) / 0.5 (Dense)

---

## MIRACL — p7_hybrid_miracl (10K)

### Quality

| Method | NDCG@10 | Recall@10 | MRR |
|--------|---------|-----------|-----|
| BM25 | 0.6385 | 0.7974 | 0.6172 |
| Dense | 0.7904 | 0.913 | 0.801 |
| RRF | 0.7683 | 0.8932 | 0.7834 |
| Bayesian | 0.7272 | 0.8706 | 0.7218 |

### Latency

| Method | p50 | p95 | p99 |
|--------|-----|-----|-----|
| BM25 | 0.44ms | 0.6ms | 0.74ms |
| Dense | 1.2ms | 1.58ms | 1.79ms |
| RRF | 1.79ms | 2.38ms | 2.7ms |
| Bayesian | 9.55ms | 12.76ms | 14.33ms |

## EZIS — p7_hybrid_ezis (97)

### Quality

| Method | NDCG@10 | Recall@10 | MRR |
|--------|---------|-----------|-----|
| BM25 | 0.9162 | 0.9847 | 0.8936 |
| Dense | 0.8041 | 0.9351 | 0.7624 |
| RRF | 0.8641 | 0.9695 | 0.829 |
| Bayesian | 0.9249 | 0.9847 | 0.9033 |

### Latency

| Method | p50 | p95 | p99 |
|--------|-----|-----|-----|
| BM25 | 0.47ms | 0.53ms | 0.55ms |
| Dense | 0.63ms | 0.75ms | 0.85ms |
| RRF | 0.92ms | 1.09ms | 1.35ms |
| Bayesian | 13.87ms | 15.01ms | 15.27ms |

---

## 비고

- **BM25**: pg_textsearch `<@>` AND matching, MeCab 토크나이저
- **Dense**: pgvector HNSW cosine (BGE-M3 1024-dim)
- **RRF**: 각 컴포넌트 top-60 후 `1/(k+rank)` 합산 (k=60)
- **Bayesian**: `to_bm25query` 실제 BM25 스코어 + cosine sim 정규화 후 α=0.5:0.5 결합
