# Phase 8: 외부 시스템 비교 — 한국어 하이브리드 검색

**생성:** 2026-03-25
**비교 대상:** PostgreSQL (Phase 7 확정 스택) vs Elasticsearch 8.17 vs Qdrant 1.15 vs Vespa 8.663

---

## 비교 시스템 요약

| 시스템 | BM25 토크나이저 | Dense | Hybrid | 비고 |
|--------|--------------|-------|--------|------|
| **PostgreSQL** | textsearch_ko MeCab (형태소) | pgvector HNSW | DB-side RRF SQL CTE | Phase 7 확정 스택 |
| **Elasticsearch 8.17** | nori (형태소, MeCab 계열) | dense_vector knn | `retriever.rrf` (서버사이드) | Trial/Platinum 라이선스 필요 |
| **Qdrant 1.15** | 없음 (self-hosted BM25 불가) | HNSW cosine | sparse IDF + dense prefetch RRF | BM25-MeCab은 TF×IDF, 진짜 BM25 아님 |
| **Vespa 8.663** | ICU (비형태소, Unicode 경계) | HNSW angular | 0.1×bm25 + closeness | 한국어 형태소 분석 미지원 |

---

## MIRACL (일반 위키, 10K docs, 213 queries)

### BM25

| 시스템 | NDCG@10 | Recall@10 | MRR | p50 | p95 |
|--------|---------|-----------|-----|-----|-----|
| **PostgreSQL** | **0.6385** | **0.7974** | **0.6172** | **0.44ms** | **0.6ms** |
| Elasticsearch | 0.61 | 0.7818 | 0.5925 | 2.47ms | 4.05ms |
| Vespa (ICU) | 0.4093 | 0.4816 | 0.4597 | 2.83ms | 3.24ms |
| Qdrant (MeCab sparse IDF) | 0.3574 | 0.5251 | 0.3643 | 1.79ms | 2.07ms |

> Qdrant Text-builtin (NDCG 0.001)과 BM42 (NDCG ~0.48 추정)은 참고용으로 별도 문서 참조.

### Dense

| 시스템 | NDCG@10 | Recall@10 | MRR | p50 | p95 |
|--------|---------|-----------|-----|-----|-----|
| PostgreSQL | 0.7904 | 0.913 | 0.801 | **1.2ms** | **1.58ms** |
| Elasticsearch | 0.7893 | 0.913 | 0.799 | 2.7ms | 3.33ms |
| Qdrant | 0.7904 | 0.913 | 0.801 | 3.16ms | 3.63ms |
| Vespa | 0.7898 | 0.913 | 0.7994 | 3.4ms | 3.85ms |

> 동일 BGE-M3 1024-dim 임베딩 사용. 품질은 동일, latency만 차이.

### Hybrid

| 시스템 | NDCG@10 | Recall@10 | MRR | p50 | p95 |
|--------|---------|-----------|-----|-----|-----|
| **PostgreSQL (RRF)** | **0.7683** | 0.8932 | **0.7834** | **1.79ms** | **2.38ms** |
| Elasticsearch (retriever.rrf) | 0.7501 | **0.8933** | 0.7387 | 5.18ms | 7.58ms |
| Qdrant (MeCab prefetch RRF) | 0.6924 | 0.8584 | 0.7019 | 4.54ms | 5.05ms |
| Vespa (0.1×bm25+closeness) | 0.4463 | 0.5391 | 0.4977 | 4.14ms | 4.9ms |

---

## EZIS (기술 문서, 97 docs, 131 queries)

### BM25

| 시스템 | NDCG@10 | Recall@10 | MRR | p50 | p95 |
|--------|---------|-----------|-----|-----|-----|
| **Elasticsearch** | **0.932** | **0.9924** | **0.9108** | 1.63ms | 2.1ms |
| PostgreSQL | 0.9162 | 0.9847 | 0.8936 | **0.47ms** | **0.53ms** |
| Vespa (ICU) | 0.8091 | 0.9427 | 0.7678 | 3.06ms | 3.44ms |
| Qdrant (MeCab sparse IDF) | 0.7721 | 0.9733 | 0.7114 | 2.09ms | 2.43ms |

### Dense

| 시스템 | NDCG@10 | Recall@10 | MRR | p50 | p95 |
|--------|---------|-----------|-----|-----|-----|
| PostgreSQL | 0.8041 | 0.9351 | 0.7624 | **0.63ms** | **0.75ms** |
| Elasticsearch | 0.8046 | 0.9351 | 0.7629 | 2.06ms | 2.49ms |
| Qdrant | 0.8041 | 0.9351 | 0.7624 | 2.53ms | 2.88ms |
| Vespa | 0.8041 | 0.9351 | 0.7624 | 3.13ms | 3.79ms |

### Hybrid

| 시스템 | NDCG@10 | Recall@10 | MRR | p50 | p95 |
|--------|---------|-----------|-----|-----|-----|
| **Elasticsearch (retriever.rrf)** | **0.8769** | **0.9847** | **0.8411** | 3.43ms | 4.45ms |
| PostgreSQL (RRF) | 0.8641 | 0.9695 | 0.829 | **0.92ms** | **1.09ms** |
| Qdrant (MeCab prefetch RRF) | 0.8394 | 0.9924 | 0.7886 | 3.69ms | 4.1ms |
| Vespa (0.1×bm25+closeness) | 0.8125 | 0.9427 | 0.7723 | 3.69ms | 4.19ms |

---

## 핵심 발견

### 1. 형태소 분석이 한국어 BM25의 핵심

| 토크나이저 유형 | 시스템 | MIRACL BM25 NDCG | EZIS BM25 NDCG |
|---------------|--------|-----------------|----------------|
| 형태소 (MeCab/nori) | PG, ES | 0.61~0.64 | 0.92~0.93 |
| 비형태소 (ICU) | Vespa | 0.41 | 0.81 |
| 없음 (TF×IDF) | Qdrant sparse | 0.36 | 0.77 |
| 트랜스포머 (BM42) | Qdrant FastEmbed | — | 0.48 |

형태소 분석기(MeCab, nori) 유무가 BM25 품질의 결정적 차이. 비형태소 시스템은 MIRACL에서 35~40% 낮음.

### 2. PostgreSQL이 latency에서 압도적

| 시스템 | BM25 p50 | Dense p50 | Hybrid p50 |
|--------|---------|----------|-----------|
| **PostgreSQL** | **0.44ms** | **1.2ms** | **1.79ms** |
| Elasticsearch | 2.47ms | 2.7ms | 5.18ms |
| Qdrant | 1.79ms | 3.16ms | 4.54ms |
| Vespa | 2.83ms | 3.4ms | 4.14ms |

PG는 네트워크 왕복 없이 DB-side에서 직접 실행. 외부 시스템 대비 2~5배 빠름.

### 3. Hybrid 품질은 BM25 품질에 종속

- PG/ES: 형태소 BM25 + Dense → Hybrid NDCG 0.75~0.88
- Qdrant: sparse IDF + Dense → Hybrid NDCG 0.69~0.84
- Vespa: ICU BM25 + Dense → Hybrid NDCG 0.45~0.81

BM25 레그가 약하면 Hybrid도 약해짐. Vespa MIRACL Hybrid(0.45)는 Dense-only(0.79)보다 나쁨.

### 4. Qdrant self-hosted에는 BM25가 없다

- `qdrant/bm25` 서버모델: Cloud 전용
- `TextIndexParams`: boolean 필터 (스코어 없음)
- `SparseVectorParams(IDF)`: TF×IDF (BM25 아님, k1/b 정규화 없음)
- FastEmbed BM25: word 토크나이저가 한국어 조사 분리 못함
- FastEmbed BM42: NDCG 0.48 (형태소 BM25의 절반)

Qdrant는 Dense-only 시스템으로 우수. 한국어 lexical search 필요 시 PG 또는 ES 필수.

---

## 결론: 시스템 선택 가이드

| 요구사항 | 추천 시스템 | 근거 |
|---------|-----------|------|
| **한국어 하이브리드 (품질 + 속도)** | **PostgreSQL** | NDCG 0.77+, p50 1.79ms, 형태소 BM25 + Dense RRF |
| **한국어 BM25 최고 품질** | **Elasticsearch** | EZIS NDCG 0.93, nori 형태소, 기술 문서 강세 |
| **Dense-only (벡터 검색)** | Qdrant 또는 PG | 동일 품질, Qdrant는 대규모 벡터 특화 |
| **비용 최소화** | PostgreSQL | 단일 DB, 추가 인프라 불필요, 가장 빠른 latency |

### Phase 7 확정 스택 유지 권장

```
textsearch_ko (MeCab) + pg_textsearch BM25 + pgvector HNSW + DB-side RRF
```

외부 시스템 대비 품질 동등 이상, latency 2~5배 우위, 운영 복잡도 최소.

---

## 상세 결과

| 시스템 | 문서 |
|--------|------|
| Elasticsearch 8.17 | [phase8_es.md](phase8_es.md) |
| Qdrant 1.15 | [phase8_qdrant.md](phase8_qdrant.md) |
| Vespa 8.663 | [phase8_vespa.md](phase8_vespa.md) |

## 기술 요약

- [O] 한국어 하이브리드 검색에서 PostgreSQL 스택이 외부 시스템 대비 동등 이상 확인
- [O] 형태소 분석기(MeCab/nori)가 한국어 BM25 품질의 핵심 요소 확인
- [O] Qdrant self-hosted는 한국어 lexical search 구조적 한계 확인
- [O] Vespa ICU 토크나이저는 한국어에 부적합 확인
- [INFO] 모든 latency는 warm-cache, retrieval-only (BGE-M3 인퍼런스 ~200ms 제외)
- [INFO] ES retriever.rrf는 Trial/Platinum 라이선스 필요
