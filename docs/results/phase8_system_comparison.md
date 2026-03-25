# Phase 8: 외부 시스템 비교 — 한국어 하이브리드 검색

**생성:** 2026-03-25
**비교 대상:** PostgreSQL (Phase 7 확정 스택) vs Elasticsearch 8.17 vs Qdrant 1.15 vs Vespa 8.663

---

## 비교 시스템 요약

| 시스템 | BM25 토크나이저 | Dense | Hybrid | 비고 |
|--------|--------------|-------|--------|------|
| **PostgreSQL** | textsearch_ko MeCab (형태소) | pgvector HNSW | DB-side RRF SQL CTE | Phase 7 확정 스택 |
| **Elasticsearch 8.17** | nori (형태소, MeCab 계열) | dense_vector knn | `retriever.rrf` (서버사이드) 또는 Python-side RRF | 서버사이드 RRF만 Trial 라이선스, Python RRF로 대체 가능 |
| **Qdrant 1.15** | 없음 (self-hosted native BM25 불가) | HNSW cosine | sparse IDF + dense prefetch RRF | 텍스트 토크나이저는 Meilisearch charabia 기반 (비형태소) |
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

### 2. AND matching에서 PG textsearch_ko가 ES nori를 압도

| 시스템 | Operator | MIRACL NDCG | EZIS NDCG |
|--------|----------|------------|----------|
| **PG textsearch_ko** | AND (`<@>`) | **0.6385** | **0.9162** |
| ES nori | AND (`operator: "and"`) | 0.1327 | 0.0153 |
| ES nori | OR (기본값) | 0.61 | 0.932 |

ES nori의 `decompound_mode: mixed`가 복합어를 과도하게 분리하여 AND matching 시 모든 토큰이 존재해야 하므로 recall이 붕괴됨. ES BM25 품질(0.61/0.93)은 OR 기본값 덕분이며, PG와 동일한 AND 조건에서는 PG가 압도적으로 우수.

### 3. PostgreSQL이 latency에서 압도적

| 시스템 | BM25 p50 | Dense p50 | Hybrid p50 |
|--------|---------|----------|-----------|
| **PostgreSQL** | **0.44ms** | **1.2ms** | **1.79ms** |
| Elasticsearch | 2.47ms | 2.7ms | 5.18ms |
| Qdrant | 1.79ms | 3.16ms | 4.54ms |
| Vespa | 2.83ms | 3.4ms | 4.14ms |

PG는 네트워크 왕복 없이 DB-side에서 직접 실행. 외부 시스템 대비 2~5배 빠름.

### 4. Hybrid 품질은 BM25 품질에 종속

- PG/ES: 형태소 BM25 + Dense → Hybrid NDCG 0.75~0.88
- Qdrant: sparse IDF + Dense → Hybrid NDCG 0.69~0.84
- Vespa: ICU BM25 + Dense → Hybrid NDCG 0.45~0.81

BM25 레그가 약하면 Hybrid도 약해짐. Vespa MIRACL Hybrid(0.45)는 Dense-only(0.79)보다 나쁨.

### 5. Qdrant self-hosted에는 native BM25가 없다

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
| **한국어 BM25 최고 품질** | **Elasticsearch** | EZIS NDCG 0.93, nori 형태소, 기술 문서 강세, 무료 라이선스로 충분 (RRF는 Python-side) |
| **Dense-only (벡터 검색)** | Qdrant 또는 PG | 동일 품질, Qdrant는 대규모 벡터 특화 |
| **비용 최소화** | PostgreSQL | 단일 DB, 추가 인프라 불필요, 가장 빠른 latency |

### Phase 7 확정 스택 유지 권장

```
textsearch_ko (MeCab) + pg_textsearch BM25 + pgvector HNSW + DB-side RRF
```

외부 시스템 대비 전체적으로 경쟁력 있는 품질, 본 실험 설정에서 latency 2~5배 우위, 운영 복잡도 최소.

> **Timescale 스택**: pg_textsearch를 개발·유지하는 Timescale이 DiskANN 기반 벡터 인덱스 [pgvectorscale](https://github.com/timescale/pgvectorscale)도 공개하고 있어, BM25(pg_textsearch) + Dense(pgvectorscale)를 동일 벤더 스택으로 구성할 수 있다. 대규모 벡터 검색에서 pgvector HNSW의 메모리 한계를 넘어야 할 때 유력한 확장 경로.
>
> **VectorChord 스택**: [VectorChord](https://github.com/tensorchord/VectorChord)도 DiskANN + Block-WeakAnd BM25를 제공하며, Phase 6-7에서 준수한 성능 확인 (100K p50 3.58ms). 다만 인덱스 구조가 neural sparse search(SPLADE, miniCOIL 등 모델 기반 sparse vector)에 더 특화되어 있고, pg_textsearch가 전통적 BM25 용도에서 더 가벼움. Neural sparse search는 모델이 토큰 가중치를 직접 생성하므로 document statistics(IDF 등)가 불필요하며, 이 경우 pgvector 기본 sparse vector 지원으로도 충분.

---

## 상세 결과

| 시스템 | 문서 |
|--------|------|
| Elasticsearch 8.17 | [phase8_es.md](phase8_es.md) |
| Qdrant 1.15 | [phase8_qdrant.md](phase8_qdrant.md) |
| Vespa 8.663 | [phase8_vespa.md](phase8_vespa.md) |

## 한계 및 주의사항

1. **PG baseline 재측정 미실시**: PG 수치는 Phase 7 실측값 재사용. 동일 시점 head-to-head가 아닌 cross-phase 비교.
2. **BM25 query semantics 불일치**: PG는 AND matching(`<@>`), ES는 OR(`match` 기본), Vespa는 `weakAnd`. 동일 BM25 연산자가 아님. 추가 실험에서 ES AND(`operator: "and"`)는 MIRACL 0.13 / EZIS 0.015로 nori 토크나이저의 과도한 복합어 분리가 AND matching에 치명적임을 확인.
3. **Hybrid fusion 방식 차이**: PG/ES/Qdrant는 RRF 계열, Vespa만 `0.1×bm25 + closeness` 선형 결합. 순수 BM25 품질 차이와 fusion 방식 차이를 분리해 증명하기 어려움.
4. **Latency 측정 조건**: warm-cache, retrieval-only, 단일 노드 로컬, 한 번에 하나의 시스템만 실행. 운영 환경 latency와 다를 수 있음.
5. **통계적 유의성 미적용**: bootstrap CI, p-value 미산출. EZIS에서 ES(0.932) vs PG(0.916) 같은 작은 차이는 통계적으로 유의하지 않을 수 있음.
6. **Vespa connection pooling 누락**: `requests.post` 매 호출 시 새 TCP 세션 → latency 과대 측정 가능성.
7. **Qdrant BM25 표현**: "BM25 불가능"이 아닌 "native self-hosted BM25 없음"이 정확. 외부 토크나이징 + sparse IDF로 ranked lexical approximation은 가능하나 품질이 낮음.

## 기술 요약

- [O] 한국어 하이브리드 검색에서 PostgreSQL 스택이 외부 시스템 대비 동등 이상 확인
- [O] 형태소 분석기(MeCab/nori)가 한국어 BM25 품질의 핵심 요소 확인
- [O] Qdrant self-hosted는 한국어 lexical search 구조적 한계 확인
- [O] Vespa ICU 토크나이저는 한국어에 부적합 확인
- [INFO] 모든 latency는 warm-cache, retrieval-only (BGE-M3 인퍼런스 ~200ms 제외)
- [INFO] ES `retriever.rrf` 서버사이드 API는 Trial/Platinum 라이선스 필요, Python-side RRF로 동일 품질 대체 가능

---

## 관련 프로젝트

### PostgreSQL 스택 (Phase 7 확정)

| 프로젝트 | 역할 | 링크 |
|---------|------|------|
| **mecab-ko** | 한국어 형태소 분석기 (MeCab 한국어 fork) | [github.com/hephaex/mecab-ko](https://github.com/hephaex/mecab-ko) |
| **textsearch_ko** (i0seph) | PostgreSQL 한국어 FTS 확장 — MeCab 기반 `korean` 텍스트 검색 설정 | [github.com/i0seph/textsearch_ko](https://github.com/i0seph/textsearch_ko) |
| **textsearch_ko** (ysys143) | textsearch_ko fork — 추가 한국어 토크나이저 지원 | [github.com/ysys143/textsearch_ko](https://github.com/ysys143/textsearch_ko) |
| **pg_textsearch** | Timescale BM25 확장 — `<@>` 연산자, BM25 인덱스 | [github.com/timescale/pg_textsearch](https://github.com/timescale/pg_textsearch) |
| **pgvector** | PostgreSQL 벡터 유사도 검색 — HNSW/IVFFlat 인덱스 | [github.com/pgvector/pgvector](https://github.com/pgvector/pgvector) |
| **pgvectorscale** | Timescale DiskANN 기반 벡터 인덱스 — pg_textsearch와 동일 팀, 시너지 우수 | [github.com/timescale/pgvectorscale](https://github.com/timescale/pgvectorscale) |
| **TimescaleDB** | PostgreSQL 시계열 확장 — metric 저장·분석에 준수한 성능, 같은 PG 인스턴스에서 검색+모니터링 통합 가능 | [timescale.com](https://www.timescale.com/) |
| **VectorChord** | DiskANN 기반 벡터 인덱스 + Block-WeakAnd BM25 — neural sparse search 특화 (아래 참고) | [github.com/tensorchord/VectorChord](https://github.com/tensorchord/VectorChord) |

### 외부 시스템

| 시스템 | 용도 | 링크 |
|--------|------|------|
| **Elasticsearch** | 분산 검색 엔진 — nori 한국어 형태소 분석기 내장 | [elastic.co](https://www.elastic.co/elasticsearch) |
| **Qdrant** | 벡터 검색 엔진 — HNSW, sparse vector, hybrid prefetch | [qdrant.tech](https://qdrant.tech/) |
| **Vespa** | 대규모 서빙 엔진 — BM25 + ANN hybrid, ICU 토크나이저 | [vespa.ai](https://vespa.ai/) |
| **FastEmbed** | 경량 임베딩 라이브러리 — BM25/BM42/SPLADE sparse 지원 | [github.com/qdrant/fastembed](https://github.com/qdrant/fastembed) |
| **BGE-M3** | 다국어 임베딩 모델 — 1024-dim dense, 본 벤치마크 전체 사용 | [huggingface.co/BAAI/bge-m3](https://huggingface.co/BAAI/bge-m3) |
