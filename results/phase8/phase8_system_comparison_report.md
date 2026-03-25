# Phase 8: 시스템 비교 — 한국어 하이브리드 검색

**Generated:** 2026-03-25 21:58:26
**PostgreSQL baseline:** Phase 7 실측 (pg_textsearch BM25 + pgvector HNSW + DB-side RRF k=60)
**Dense:** BGE-M3 1024-dim, retrieval-only (인퍼런스 제외)

---

## 비교 시스템

| 시스템 | BM25 토크나이저 | Dense | 하이브리드 |
|--------|--------------|-------|----------|
| **PostgreSQL** | textsearch_ko MeCab (형태소) | pgvector HNSW | DB-side RRF SQL CTE |
| **Elasticsearch** | nori (형태소, MeCab 계열) | dense_vector knn | ES RRF retriever |
| **Qdrant 1.15.x** | MeCab sparse vector (IDF) | HNSW cosine | Qdrant prefetch RRF |
| **Qdrant-builtin** | multilingual tokenizer (Unicode, 비형태소) | — | — |

> Qdrant Text-builtin은 charabia Unicode word boundary — 형태소 분석 아님.
> BM25 품질은 MeCab 기반보다 낮을 것으로 예상.

---

## MIRACL

#### MIRACL — BM25

| System | NDCG@10 | Recall@10 | MRR | p50 | p95 |
|--------|---------|-----------|-----|-----|-----|
| PostgreSQL (pg_textsearch+pgvector) | 0.6385 | 0.7974 | 0.6172 | 0.44ms | 0.6ms |
| Elasticsearch (nori) | 0.61 | 0.7818 | 0.5925 | 2.47ms | 4.05ms |
| Vespa | 0.4093 | 0.4816 | 0.4597 | 2.83ms | 3.24ms |

#### MIRACL — BM25-MeCab

| System | NDCG@10 | Recall@10 | MRR | p50 | p95 |
|--------|---------|-----------|-----|-----|-----|
| Qdrant 1.15.x | 0.3574 | 0.5251 | 0.3643 | 1.79ms | 2.07ms |

#### MIRACL — Dense

| System | NDCG@10 | Recall@10 | MRR | p50 | p95 |
|--------|---------|-----------|-----|-----|-----|
| PostgreSQL (pg_textsearch+pgvector) | 0.7904 | 0.913 | 0.801 | 1.2ms | 1.58ms |
| Elasticsearch (nori) | 0.7893 | 0.913 | 0.799 | 2.7ms | 3.33ms |
| Qdrant 1.15.x | 0.7904 | 0.913 | 0.801 | 3.16ms | 3.63ms |
| Vespa | 0.7898 | 0.913 | 0.7994 | 3.4ms | 3.85ms |

#### MIRACL — Hybrid

| System | NDCG@10 | Recall@10 | MRR | p50 | p95 |
|--------|---------|-----------|-----|-----|-----|
| PostgreSQL (pg_textsearch+pgvector) | 0.7683 | 0.8932 | 0.7834 | 1.79ms | 2.38ms |
| Elasticsearch (nori) | 0.7501 | 0.8933 | 0.7387 | 5.18ms | 7.58ms |
| Vespa | 0.4463 | 0.5391 | 0.4977 | 4.14ms | 4.9ms |

#### MIRACL — Hybrid-MeCab

| System | NDCG@10 | Recall@10 | MRR | p50 | p95 |
|--------|---------|-----------|-----|-----|-----|
| Qdrant 1.15.x | 0.6924 | 0.8584 | 0.7019 | 4.54ms | 5.05ms |

## EZIS

#### EZIS — BM25

| System | NDCG@10 | Recall@10 | MRR | p50 | p95 |
|--------|---------|-----------|-----|-----|-----|
| PostgreSQL (pg_textsearch+pgvector) | 0.9162 | 0.9847 | 0.8936 | 0.47ms | 0.53ms |
| Elasticsearch (nori) | 0.932 | 0.9924 | 0.9108 | 1.63ms | 2.1ms |
| Vespa | 0.8091 | 0.9427 | 0.7678 | 3.06ms | 3.44ms |

#### EZIS — BM25-MeCab

| System | NDCG@10 | Recall@10 | MRR | p50 | p95 |
|--------|---------|-----------|-----|-----|-----|
| Qdrant 1.15.x | 0.7721 | 0.9733 | 0.7114 | 2.09ms | 2.43ms |

#### EZIS — Dense

| System | NDCG@10 | Recall@10 | MRR | p50 | p95 |
|--------|---------|-----------|-----|-----|-----|
| PostgreSQL (pg_textsearch+pgvector) | 0.8041 | 0.9351 | 0.7624 | 0.63ms | 0.75ms |
| Elasticsearch (nori) | 0.8046 | 0.9351 | 0.7629 | 2.06ms | 2.49ms |
| Qdrant 1.15.x | 0.8041 | 0.9351 | 0.7624 | 2.53ms | 2.88ms |
| Vespa | 0.8041 | 0.9351 | 0.7624 | 3.13ms | 3.79ms |

#### EZIS — Hybrid

| System | NDCG@10 | Recall@10 | MRR | p50 | p95 |
|--------|---------|-----------|-----|-----|-----|
| PostgreSQL (pg_textsearch+pgvector) | 0.8641 | 0.9695 | 0.829 | 0.92ms | 1.09ms |
| Elasticsearch (nori) | 0.8769 | 0.9847 | 0.8411 | 3.43ms | 4.45ms |
| Vespa | 0.8125 | 0.9427 | 0.7723 | 3.69ms | 4.19ms |

#### EZIS — Hybrid-MeCab

| System | NDCG@10 | Recall@10 | MRR | p50 | p95 |
|--------|---------|-----------|-----|-----|-----|
| Qdrant 1.15.x | 0.8394 | 0.9924 | 0.7886 | 3.69ms | 4.1ms |

---

## 비고

- **Dense latency**: 모든 시스템에서 retrieval-only (BGE-M3 인퍼런스 ~200ms 제외)
- **Qdrant BM25 한계**: self-hosted Qdrant에는 BM25 구현 없음. `qdrant/bm25` 서버모델은 Cloud 전용, `TextIndexParams`는 unranked 필터, sparse vector IDF는 BM25 아님 (문서 길이 정규화 k1/b 미적용)
- **Qdrant BM25-MeCab (참고)**: MeCab 외부 토크나이징 + SparseVectorParams(modifier=IDF) — TF×IDF일 뿐 진짜 BM25가 아니므로 품질 저하 (MIRACL NDCG 0.36)
- **Qdrant Hybrid-MeCab**: prefetch RRF (sparse IDF + dense) — sparse 품질 한계로 PG/ES hybrid 대비 열위
- **ES Hybrid**: `retriever.rrf` (rank_window_size=60, rank_constant=60)
- **PG Hybrid**: DB-side SQL CTE RRF (k=60, topk=60)
- **Vespa BM25**: ICU 토크나이저 (비형태소) — 한국어 형태소 분석 없이 단어 경계 분리만 수행
