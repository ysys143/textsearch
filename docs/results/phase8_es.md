# Phase 8: Elasticsearch 8.17.0 (nori) — 한국어 형태소 기반 BM25/Dense/Hybrid 비교

**생성:** 2026-03-25

---

## 섹션 1 — 인프라 컨텍스트

### Elasticsearch 8.17.0 스택

- **엔진**: Elasticsearch 8.17.0
- **어휘 기반(Lexical)**: BM25 + nori 분석기
  - **nori**: MeCab 계열 한국어 형태소 분석기
  - **decompound_mode**: `mixed` (합성어 분해)
  - **Korean morphological decomposition**: 자동 활성화
- **밀집(Dense)**: ES native `dense_vector` 1024차원
  - **코사인 유사도**: `knn` 검색
  - **임베딩**: BGE-M3 (Phase 7 동일)
- **하이브리드(Hybrid)**: ES `retriever.rrf` API
  - **RRF 파라미터**: `rank_window_size=60`, `rank_constant=60`
  - **라이선스 필요**: Trial 또는 Platinum (무료는 불가)
  - **구현**: ES 네이티브 API (외부 병합 불필요)

### nori 분석기 특성

nori는 MeCab 기반 한국어 형태소 분석기로, 다음과 같은 특징을 가집니다:
- **형태소 분석**: 어근, 품사 기반 토크나이제이션
- **합성어 분해**: decompound_mode `mixed` 설정으로 자동 분해
- **POS 태깅**: 불용어 제거 가능 (조사, 어미 등)
- Phase 7의 textsearch_ko와 동일 계열의 분석기이지만, ES의 inverted index와 결합되므로 인덱싱 특성이 상이

---

## 섹션 2 — MIRACL 벤치마크 (10K 문서, 213 쿼리)

### 품질 메트릭

| 방법 | NDCG@10 | Recall@10 | MRR  |
|------|---------|-----------|------|
| BM25 (nori) | 0.61 | 0.7818 | 0.5925 |
| Dense (knn) | 0.7893 | 0.913 | 0.799 |
| Hybrid (retriever.rrf) | 0.7501 | 0.8933 | 0.7387 |

### 지연시간 (ms)

| 방법 | p50 | p95 | p99 |
|------|-----|-----|-----|
| BM25 (nori) | 2.47 | 4.05 | 13.98 |
| Dense (knn) | 2.7 | 3.33 | 3.67 |
| Hybrid (retriever.rrf) | 5.18 | 7.58 | 8.84 |

### 분석

#### BM25 품질: Phase 7 대비 소폭 하락

- **ES BM25**: NDCG 0.61, Phase 7 pg_textsearch: NDCG 0.6385
- **하락폭**: -2.6% (0.0285)

ES nori 기반 BM25는 Phase 7 PostgreSQL textsearch_ko보다 약간 낮은 점수를 기록합니다. 이는 다음 요인 때문일 수 있습니다:
- BM25 파라미터 튜닝 차이 (ES 기본값 vs PG 맞춤 설정)
- 형태소 분석 알고리즘의 미묘한 차이 (MeCab 구현체 차이)
- inverted index 구성 방식의 차이

#### EZIS 품질: Phase 7 대비 소폭 상향

- **ES BM25**: NDCG 0.932, Phase 7 pg_textsearch: NDCG 0.9162
- **상향폭**: +1.7% (0.0158)

기술 문서(EZIS)에서는 ES nori가 PG textsearch_ko를 약간 앞섭니다. 합성어 분해(decompound_mode: mixed)가 기술 용어 매칭에 유리한 것으로 보입니다.

#### Dense는 동일 임베딩, 거의 동일한 품질

- **MIRACL**: ES 0.7893, Phase 7 0.7904 (차이 -0.14%)
- **EZIS**: ES 0.8046, Phase 7 0.8041 (차이 +0.06%)

동일한 BGE-M3 임베딩을 사용하므로 품질은 거의 동일합니다.

#### Hybrid 품질: RRF 비일관적 성능

- **MIRACL RRF**: NDCG 0.7501 (Dense 0.7893보다 5% 낮음)
- **EZIS RRF**: NDCG 0.8769 (BM25 0.932보다 6% 낮음)

ES의 retriever.rrf는 도메인 무관 "2등 전략"으로 작동하지만, Phase 7의 SQL CTE 기반 RRF(MIRACL 0.7683, EZIS 0.8641)보다 약간 낮습니다. 이는 ES RRF의 rank_window_size/rank_constant 하이퍼파라미터가 최적화되지 않았을 가능성을 시사합니다.

---

## 섹션 3 — EZIS 벤치마크 (97 문서, 131 쿼리)

### 품질 메트릭

| 방법 | NDCG@10 | Recall@10 | MRR  |
|------|---------|-----------|------|
| BM25 (nori) | 0.932 | 0.9924 | 0.9108 |
| Dense (knn) | 0.8046 | 0.9351 | 0.7629 |
| Hybrid (retriever.rrf) | 0.8769 | 0.9847 | 0.8411 |

### 지연시간 (ms)

| 방법 | p50 | p95 | p99 |
|------|-----|-----|-----|
| BM25 (nori) | 1.63 | 2.1 | 2.75 |
| Dense (knn) | 2.06 | 2.49 | 2.64 |
| Hybrid (retriever.rrf) | 3.43 | 4.45 | 6.5 |

### 분석

#### BM25 지배적: 기술 문서에서 최우수

기술 매뉴얼(EZIS) 도메인에서 BM25가 명확히 우수합니다:
- NDCG 0.932 (1위)
- Dense 0.8046에 비해 15.8% 높음

이는 기술 문서의 정확한 용어 매칭이 의미론적 임베딩보다 중요함을 시사합니다.

#### Hybrid는 "안정적 2등"

RRF는 NDCG 0.8769로 BM25와 Dense의 중간값이며, 튜닝 없이 도메인 무관 "reasonable default"로 작동합니다.

---

## 섹션 4 — 지연시간 비교 (Phase 7 대비)

### 절대값 비교 (MIRACL 10K 기준)

| 방법 | Phase 7 (PG) | Phase 8 (ES) | 배수 |
|------|------------|------------|------|
| BM25 p50 | 0.44ms | 2.47ms | 5.6x |
| Dense p50 | 1.20ms | 2.7ms | 2.25x |
| Hybrid (RRF) p50 | 1.79ms | 5.18ms | 2.9x |

### 분석

ES는 PG 대비 **2~5배 느립니다**. 원인:
1. **네트워크 오버헤드**: ES 클라이언트-서버 왕복 (HTTP/JSON)
2. **JVM 오버헤드**: Java 가상머신 스택
3. **inverted index 구성**: Lucene 기반 인덱싱 구조
4. **쿼리 파싱/분석 오버헤드**: nori 분석기 리얼타임 실행

#### Dense 지연시간 주의

> [WARN] **Dense/Hybrid 지연시간 주의**: 위 수치는 **retrieval-only** 측정값입니다. BGE-M3 온라인 추론 시간(실제 ~200ms+)은 포함되지 않습니다. 프로덕션 환경에서는 쿼리 임베딩 추론 비용(200ms 이상)을 별도로 고려해야 합니다.

---

## 섹션 5 — 도메인별 역전 현상 (Phase 7 확인)

### 데이터셋

| 도메인 | 최고 방법 (NDCG) | 2순위 | 지연(p50) |
|--------|----------------|------|---------|
| MIRACL (일반 위키) | Dense (0.7893) | Hybrid (0.7501) | 2.7ms |
| EZIS (기술 문서) | BM25 (0.932) | Hybrid (0.8769) | 1.63ms |

**결론**: Phase 7과 동일 패턴:
- **일반 텍스트**: 의미론적 Dense가 우수
- **기술 문서**: 정확한 용어 BM25가 우수

---

## 섹션 6 — 각 검색 방법의 특성

### BM25 (nori)

**장점**:
- 기술 문서(EZIS)에서 최우수 품질 (NDCG 0.932)
- 지연시간 안정적 (MIRACL 2.47ms, EZIS 1.63ms)
- 형태소 기반 정확한 매칭 (합성어 분해)

**단점**:
- 일반 위키(MIRACL)에서 품질 낮음 (NDCG 0.61)
- Phase 7 PG 대비 소폭 하락 (NDCG -2.6%)

**추천**: 기술 매뉴얼, 정규화된 용어 집합 도메인에서만 사용

### Dense (knn)

**장점**:
- MIRACL에서 최우수 품질 (NDCG 0.7893)
- 의미론적 유사성 포착 (paraphrase, synonymy)
- 안정적인 지연시간 (2.7ms)

**단점**:
- 온라인 추론 비용 미포함 (실제 200ms+ 추가)
- EZIS에서 품질 낮음 (NDCG 0.8046)
- 용어 매칭 약함

**추천**: 일반 텍스트 검색, 다국어 시나리오

### Hybrid (retriever.rrf)

**장점**:
- 도메인 무관 견고한 성능 (MIRACL 0.7501, EZIS 0.8769)
- 튜닝 불필요 (ES 네이티브 API)
- 1등 선택이 아니지만 "reasonable default"

**단점**:
- 가장 높은 지연시간 (5.18ms @MIRACL, 3.43ms @EZIS)
- 라이선스 필요 (Trial 또는 Platinum)
- Phase 7 SQL RRF 대비 품질 소폭 하락

**추천**: 도메인 사전정보 없을 때, 하이브리드 필수인 경우

---

## 섹션 7 — PostgreSQL vs Elasticsearch 최종 비교

### 검색 품질 (NDCG@10)

#### MIRACL (일반 위키)

| 방법 | Phase 7 (PG) | Phase 8 (ES) | 차이 |
|------|------------|------------|------|
| BM25 | 0.6385 | 0.61 | -2.6% |
| Dense | 0.7904 | 0.7893 | -0.14% |
| Hybrid (RRF) | 0.7683 | 0.7501 | -2.4% |

**승자**: Phase 7 PG (모든 방법 동등 또는 우수)

#### EZIS (기술 문서)

| 방법 | Phase 7 (PG) | Phase 8 (ES) | 차이 |
|------|------------|------------|------|
| BM25 | 0.9162 | 0.932 | +1.7% |
| Dense | 0.8041 | 0.8046 | +0.06% |
| Hybrid (RRF) | 0.8641 | 0.8769 | +1.5% |

**승자**: Phase 8 ES (모든 방법 소폭 우수)

### 지연시간 (p50, ms)

#### MIRACL 10K

| 방법 | Phase 7 (PG) | Phase 8 (ES) | 배수 |
|------|------------|-----------|------|
| BM25 | 0.44 | 2.47 | 5.6x |
| Dense | 1.20 | 2.7 | 2.25x |
| Hybrid (RRF) | 1.79 | 5.18 | 2.9x |

**승자**: Phase 7 PG (2~5배 빠름)

#### EZIS 97

| 방법 | Phase 7 (PG) | Phase 8 (ES) | 배수 |
|------|------------|-----------|------|
| BM25 | 0.47 | 1.63 | 3.5x |
| Dense | 0.63 | 2.06 | 3.3x |
| Hybrid (RRF) | 0.92 | 3.43 | 3.7x |

**승자**: Phase 7 PG (3~4배 빠름)

### 종합 평가

| 차원 | 우수자 | 이유 |
|------|--------|------|
| **검색 품질** | **동등** (도메인별 차이) | 일반 문서는 PG, 기술 문서는 ES 소폭 우수 |
| **지연시간** | **Phase 7 PG** | 2~5배 빠름 (네트워크+JVM 오버헤드 차이) |
| **운영 편의성** | **Phase 8 ES** | 분산 처리, 수평 확장, 클라우드 친화적 |
| **비용 효율** | **Phase 7 PG** | 단일 머신, 라이선스 불필요 (Hybrid 제외) |
| **개발 속도** | **Phase 8 ES** | 네이티브 RRF, 하이브리드 API 기본 제공 |

---

## 섹션 8 — 결론 및 선택 기준

### Phase 7 (PostgreSQL) 선택 조건

- **지연시간 민감**: 서브 ms 응답 요구 (실시간 검색)
- **단일 데이터센터**: 분산 처리 불필요
- **BM25 품질 우선**: MIRACL 같은 일반 문서에서 우수 필요
- **운영 비용 절감**: 라이선스 없는 자체 호스팅 선호
- **하이브리드 불필수**: Bayesian 또는 RRF 튜닝 가능

### Phase 8 (Elasticsearch) 선택 조건

- **분산 처리**: 수평 확장, 멀티 노드 운영 필요
- **기술 문서**: EZIS 같은 정규 용어 집합에서 1~2% 품질 향상 추구
- **개발 속도**: 하이브리드 검색, RRF/Bayesian 네이티브 API 활용
- **클라우드 친화성**: Elastic Cloud, AWS managed 서비스 선호
- **지연시간 허용**: 2~5ms 추가 오버헤드 감수 가능

---

## 섹션 9 — 기술 요약

**Phase 8은 Elasticsearch 8.17.0 (nori) 기반 한국어 검색 벤치마크입니다.**

### 검증 항목

- [O] **nori 형태소 분석**: MeCab 기반, decompound_mode: mixed로 합성어 분해 지원
- [O] **BM25 안정성**: MIRACL 0.61 (PG -2.6%) / EZIS 0.932 (PG +1.7%)
- [O] **Dense 일관성**: BGE-M3 동일 임베딩, MIRACL 0.7893 / EZIS 0.8046
- [O] **Hybrid (RRF)**: ES 네이티브 API, 도메인 무관 "reasonable default" (0.75~0.88 NDCG)
- [O] **지연시간 특성**: PG 대비 2~5배 느림 (네트워크+JVM 오버헤드)
- [O] **라이선스 제약**: Hybrid(retriever.rrf)는 Trial 또는 Platinum 필요

### 한계 및 다음 단계

- [INFO] **온라인 추론 미포함**: Dense/Hybrid 지연시간은 retrieval-only이며, BGE-M3 추론 200ms+ 미포함
- [INFO] **단일 배포**: 로컬 Elasticsearch 기본 배포, 프로덕션 분산 설정 미검증
- [PENDING] **Qdrant 1.15.x**: Phase 9에서 sparse vector (MeCab IDF) + HNSW dense + RRF 비교
- [PENDING] **Vespa**: Phase 9에서 ICU tokenizer + BM25 + HNSW + linear hybrid score 비교

**결론**: Phase 7 PostgreSQL과 Phase 8 Elasticsearch는 **지연시간-품질 트레이드오프**를 명확히 보여줍니다. 기술 문서 도메인에서 ES가 소폭 우수하지만, 응답시간과 운영 복잡도를 고려하면 **자체 호스팅 환경에서는 PG, 분산 처리 요구 환경에서는 ES**를 권장합니다.

---

## 부록 — 측정 환경 메모

- **테스트 날짜**: 2026-03-25
- **ES 버전**: 8.17.0
- **nori 설정**: `decompound_mode: mixed`
- **dense_vector**: 1024차원 cosine similarity (BGE-M3)
- **retriever.rrf**: rank_window_size=60, rank_constant=60
- **데이터**: MIRACL 10K (213 쿼리) + EZIS 97 (131 쿼리)
- **임베딩**: BGE-M3 (Phase 7 동일)
- **측정 방식**: warm-cache, retrieval-only (임베딩 추론 제외)
