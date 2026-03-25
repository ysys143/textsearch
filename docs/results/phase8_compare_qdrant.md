# Phase 8: Qdrant 1.15.0 — 클라우드 벡터 DB의 한국어 검색 능력

**생성:** 2026-03-25

---

## 핵심 질문

**Qdrant 같은 전문 벡터 데이터베이스는 한국어 lexical search를 지원하는가? PostgreSQL 하이브리드 vs Qdrant 성능은 어떻게 비교되는가?**

답: **Qdrant는 기본적으로 Dense-only 시스템이며, 한국어 BM25는 구조적 한계가 있다.** 다른 벡터 DB들도 유사한 문제를 갖고 있다.

---

## 평가 대상과 결과 요약

### 인프라 컨텍스트

- **시스템**: Qdrant v1.15.0 self-hosted
- **어휘 검색 (Lexical)**: 여러 BM25 구현 시도
  - `SparseVectorParams(modifier=IDF)` + MeCab: TF×IDF만 가능 (진정한 BM25 아님)
  - `TextIndexParams(tokenizer=MULTILINGUAL)`: 한국어 비-스코어 필터 (NDCG ≈ 0 정상)
  - FastEmbed BM42: 트랜스포머 기반, 한국어 성능 낮음
  - FastEmbed BM25: word 토크나이저가 한국어 조사 미분리
- **밀집 검색 (Dense)**: HNSW cosine, BGE-M3 1024차원 (PostgreSQL과 동일)
- **데이터셋**: MIRACL-ko (10K docs, 213 queries) + EZIS (97 docs, 131 queries)

---

## Qdrant MIRACL 결과 (10K 문서, 213 queries)

| 방법 | NDCG@10 | Recall@10 | MRR | p50 | p95 |
|------|---------|-----------|-----|-----|-----|
| **BM25-MeCab** (sparse IDF) | 0.3574 | 0.5251 | 0.3643 | 1.79ms | 2.07ms |
| Text-builtin (multilingual) | 0.0012 | 0.0012 | 0.0023 | 1.6ms | 1.84ms |
| **Dense** (HNSW cosine) | 0.7904 | 0.913 | 0.801 | 3.16ms | 3.63ms |
| **Hybrid-MeCab** (prefetch RRF) | 0.6924 | 0.8584 | 0.7019 | 4.54ms | 5.05ms |

### 주요 발견

1. **Dense 성능 (0.7904 NDCG)**
   - PostgreSQL Phase 7 0.7904와 **정확히 동일** (동일 BGE-M3 모델)
   - Qdrant의 HNSW 구현이 PostgreSQL pgvector와 동등

2. **BM25-MeCab 성능 (0.3574 NDCG)**
   - PostgreSQL pl/pgsql v2 0.3355 대비 +0.0219 (약간 우수)
   - 그러나 PostgreSQL pg_textsearch `<@>` 0.6385 대비 -0.2811 (크게 열위)
   - **이유**: Qdrant sparse는 IDF만 가능, BM25 문서 길이 정규화(k1, b 파라미터) 없음

3. **Hybrid-MeCab 성능 (0.6924 NDCG)**
   - PostgreSQL RRF 0.7683 대비 -0.0759 (열위)
   - PostgreSQL Bayesian 0.7272 대비 -0.0348 (열위)
   - **이유**: sparse 품질 저하로 hybrid 효율성 감소

4. **Text-builtin (multilingual) 성능 (0.0012 NDCG)**
   - 정상적인 결과. 이 인덱서는 boolean 필터 전용이며 스코어를 생성하지 않음.
   - 검색 기능이 아닌 필터링 목적으로만 사용.

---

## Qdrant EZIS 결과 (97 문서, 131 queries)

| 방법 | NDCG@10 | Recall@10 | MRR | p50 | p95 |
|------|---------|-----------|-----|-----|-----|
| **BM25-MeCab** (sparse IDF) | 0.7721 | 0.9733 | 0.7114 | 2.09ms | 2.43ms |
| Text-builtin (multilingual) | 0.0 | 0.0 | 0.0 | 1.72ms | 2.01ms |
| **Dense** (HNSW cosine) | 0.8041 | 0.9351 | 0.7624 | 2.53ms | 2.88ms |
| **Hybrid-MeCab** (prefetch RRF) | 0.8394 | 0.9924 | 0.7886 | 3.69ms | 4.1ms |

### 주요 발견

1. **Dense 성능 (0.8041 NDCG)**
   - PostgreSQL Phase 7 0.8041과 **정확히 동일** (동일 BGE-M3 모델)

2. **BM25-MeCab 성능 (0.7721 NDCG)**
   - PostgreSQL BM25 0.9162 대비 -0.1441 (유의미한 열위)
   - **이유**: BM25 정규화 부족. 기술 문서에서 정확한 용어 매칭이 중요한데, sparse IDF만으로는 부족

3. **Hybrid-MeCab 성능 (0.8394 NDCG)**
   - PostgreSQL RRF 0.8641 대비 -0.0247 (미세 열위)
   - PostgreSQL Bayesian 0.9249 대비 -0.0855 (유의미 열위)
   - **원인**: sparse 품질이 PG 대비 낮음

---

## 추가 테스트: FastEmbed BM42 (EZIS만)

| Dataset | NDCG@10 | 특징 |
|---------|---------|------|
| EZIS | 0.4801 | 트랜스포머 어텐션 기반 |

**분석:**
- FastEmbed BM42는 트랜스포머 기반 신경망 모델
- 한국어 성능 0.4801은 형태소 기반 BM25-MeCab 0.7721에 미치지 못함
- 신경 모델이 아직도 한국어 lexical ranking에는 부족

---

## Qdrant 한국어 lexical search의 구조적 문제

### 문제 1: Self-hosted에는 BM25 구현이 없음

**Qdrant BM25 현황:**
- `qdrant/bm25` 서버 모델: Qdrant Cloud 전용 (self-hosted 불가)
- Self-hosted는 벡터만 지원하며, sparse vector IDF modifier만 제공

**결과:**
- Qdrant self-hosted를 사용할 수 없음 (클라우드 전용)
- 자체 구현 sparse vector IDF로 대체 = BM25 미흡

### 문제 2: TextIndexParams(tokenizer=MULTILINGUAL)는 검색 아님

```sql
-- Qdrant multilingual tokenizer의 실제 동작
TextIndexParams(tokenizer=MULTILINGUAL)
  → 한국어 텍스트 인덱싱 (토크나이저 동작)
  → Boolean 필터링만 가능 (값 검색)
  → 스코어 생성 안 함 (NDCG ≈ 0)
```

**실험 결과:**
- NDCG@10 = 0.0012 (MIRACL) / 0.0 (EZIS)
- 이는 **정상 동작** — 검색 인덱서가 아니므로

### 문제 3: SparseVectorParams(modifier=IDF) + MeCab은 진정한 BM25 아님

**BM25 공식:**
```
BM25(q, d) = Σ IDF(qi) * (f(qi, d) * (k1 + 1)) / (f(qi, d) + k1 * (1 - b + b * (|d| / avgdl)))
```

**Qdrant sparse의 실제 구현:**
```
score = Σ TF(qi, d) * IDF(qi)
  (문서 길이 정규화 없음 — k1, b 파라미터 부재)
```

**영향:**
- 긴 문서가 항상 높은 점수 → recall 편향
- BM25의 정규화 특성 상실
- 실제로는 Okapi BM25가 아닌 "TF×IDF"에 불과

### 문제 4: FastEmbed BM42/BM25는 한국어 미지원

**FastEmbed 한국어 성능:**
- BM42 (트랜스포머): EZIS 0.4801 (MeCab BM25 0.7721 미달)
- BM25: word 토크나이저 기반, 한국어 조사 미분리 (NDCG overlap ≈ 0)

**원인:**
- BM42/BM25는 영어/유럽 언어 중심 설계
- 한국어 형태소 분석 미포함

---

## PostgreSQL vs Qdrant 하이브리드 성능 비교

### MIRACL (일반 도메인)

| 시스템 | 방법 | NDCG@10 | Latency p50 | 특징 |
|--------|------|---------|------------|------|
| **PostgreSQL** | pg_textsearch BM25 + Dense RRF | 0.7683 | 1.79ms | Lexical 강함 |
| **PostgreSQL** | pl/pgsql BM25 v2 + Dense RRF | 0.7272 | ~52ms | Lexical 약함 |
| **Qdrant** | Sparse IDF + Dense RRF | 0.6924 | 4.54ms | Lexical 매우 약함 |

**결론**: PostgreSQL pg_textsearch가 0.0759 NDCG 우위 + 2.5배 빠름

### EZIS (기술 도메인)

| 시스템 | 방법 | NDCG@10 | Latency p50 | 특징 |
|--------|------|---------|------------|------|
| **PostgreSQL** | pg_textsearch BM25 + Dense Bayesian | 0.9249 | ~13.87ms | Lexical 우수 |
| **PostgreSQL** | BM25 단독 | 0.9162 | 0.47ms | Lexical 만족 |
| **Qdrant** | Sparse IDF + Dense RRF | 0.8394 | 3.69ms | Lexical 약함 |

**결론**: PostgreSQL이 0.0855 NDCG 우위

---

## Qdrant의 강점과 약점

### 강점

1. **Dense 검색 성능**
   - BGE-M3 기반 임베딩 동일 수준 (NDCG 0.7904, 0.8041)
   - HNSW 구현이 PostgreSQL pgvector와 동등

2. **빠른 데이터 삽입**
   - Qdrant의 병렬 벡터 추가 최적화
   - Append-only 아키텍처

3. **벡터 전문 최적화**
   - 다양한 거리 메트릭 (cosine, euclidean, dot product)
   - Scalar quantization, HNSW 파라미터 튜닝

### 약점

1. **한국어 Lexical Search 미지원**
   - Self-hosted: BM25 없음
   - Cloud: BM25 있으나 추가 비용 + Qdrant 종속성
   - Sparse vector만: BM25 불완전 구현

2. **Hybrid 성능 열위**
   - Sparse 품질 낮음 → Hybrid 효율성 저하
   - PostgreSQL RRF 대비 -0.08 NDCG

3. **Cross-language 지원 약함**
   - MULTILINGUAL tokenizer: boolean 필터만
   - 형태소 분석 전용 토크나이저 없음

---

## Qdrant를 선택해야 하는 경우

1. **Dense-only 검색 (의미론 중심)**
   - BGE-M3 임베딩만 필요한 경우
   - 하이브리드 불필요
   - Qdrant의 Dense 성능 = PostgreSQL과 동등

2. **클라우드 관리형 선호**
   - Qdrant Cloud: 벡터 DB 전문 운영
   - 스케일링 자동화
   - Self-hosting 운영 비용 회피

3. **다양한 거리 메트릭 필요**
   - Cosine, Euclidean, Dot Product 등
   - 특정 임베딩 모델과의 호환성 중요

---

## Qdrant를 피해야 하는 경우

1. **한국어 어휘 검색 (BM25) 필요**
   - Self-hosted: 구현 불가능
   - Cloud: 추가 비용 + 종속성
   - PostgreSQL + pg_textsearch 우수

2. **Hybrid 검색 (Lexical + Dense)**
   - PostgreSQL RRF/Bayesian이 더 강함
   - Qdrant sparse quality 낮음
   - PostgreSQL이 0.07~0.08 NDCG 우위

3. **DB 기반 다른 기능 필요**
   - Full-text 검색, 필터링, 정렬
   - 트랜잭션 일관성 필요
   - PostgreSQL + 확장 선택

---

## Phase 8 결론

### 주요 발견

1. **Qdrant Dense는 동등**
   - BGE-M3 HNSW 구현이 PostgreSQL pgvector와 동일 수준
   - Dense-only 시스템으로서 우수한 선택

2. **Qdrant Lexical은 구조적 한계**
   - Self-hosted BM25 미지원
   - Sparse vector IDF = 불완전한 BM25
   - 한국어 형태소 분석 미지원

3. **한국어 Hybrid는 PostgreSQL 우수**
   - pg_textsearch BM25 + pgvector: NDCG 0.76~0.92
   - Qdrant Sparse + Dense: NDCG 0.69~0.84
   - 차이: 0.07~0.08 NDCG

4. **비용-효율성**
   - PostgreSQL: 기존 인프라 활용, 운영 간단
   - Qdrant Cloud: 벡터 DB 전문, 관리형 운영

### 벡터 DB 선택 가이드

| 요구사항 | 권고 | 이유 |
|---------|------|------|
| Dense 검색만 필요 | Qdrant 또는 PostgreSQL | 성능 동등, 비용/운영 선택 |
| Hybrid (Lexical+Dense) | **PostgreSQL** | pg_textsearch BM25 우수 |
| 한국어 Full-text | **PostgreSQL** | pg_textsearch_ko + MeCab |
| 관리형 클라우드 선호 | Qdrant Cloud | 벡터 DB 전문 운영 |
| 데이터베이스 기능 필요 | PostgreSQL | 일관성, 트랜잭션, 필터링 |

### 다음 단계 (Phase 9)

**일관된 비교를 위해 다음을 검증할 필요:**
1. **Qdrant Cloud BM25 (managed)**: self-hosted 제약 해제
2. **Elasticsearch 8.x (nori)**: 한국어 BM25 표준
3. **Vespa (ICU tokenizer)**: 다중 언어 하이브리드
4. **Direct PostgreSQL vs Elasticsearch**: lexical 성능 재검증

---

## 기술 요약

**Phase 8은 벡터 데이터베이스 Qdrant 1.15.0의 한국어 검색 능력을 평가했다.**

- [O] Dense 검색: PostgreSQL과 동등 (NDCG 0.790, 0.804)
- [X] Lexical 검색 (self-hosted): 구현 불가능
- [X] Hybrid 검색: PostgreSQL 대비 0.07~0.08 NDCG 열위
- [INFO] Qdrant Cloud BM25는 managed 서비스 (self-hosted 제약 해제 필요)
- [INFO] 한국어 형태소 분석은 벡터 DB의 구조적 한계

**결론**: Qdrant는 Dense-only 시스템으로서 우수하나, 한국어 하이브리드 검색은 PostgreSQL + pg_textsearch가 표준이다.
