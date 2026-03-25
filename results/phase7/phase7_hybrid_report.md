# Phase 7 Section 2: Hybrid Search Benchmark

**Generated:** 2026-03-25 20:13:45
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
| BM25 | 0.49ms | 0.69ms | 0.84ms |
| Dense | 1.22ms | 1.5ms | 1.93ms |
| RRF | 1.95ms | 2.45ms | 2.76ms |
| Bayesian | 8.84ms | 11.93ms | 13.44ms |

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
| BM25 | 0.44ms | 0.52ms | 0.6ms |
| Dense | 0.6ms | 0.68ms | 0.84ms |
| RRF | 1.11ms | 1.3ms | 1.44ms |
| Bayesian | 14.09ms | 16.3ms | 16.91ms |

---

## 비고

- **BM25**: pg_textsearch `<@>` AND matching, MeCab 토크나이저
- **Dense**: pgvector HNSW cosine (BGE-M3 1024-dim)
- **RRF**: 각 컴포넌트 top-60 후 `1/(k+rank)` 합산 (k=60)
- **Bayesian**: `to_bm25query` 실제 BM25 스코어 + cosine sim 정규화 후 α=0.5:0.5 결합
