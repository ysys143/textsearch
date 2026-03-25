# Phase 7: pg_textsearch 스케일링 & 하이브리드 검색 벤치마크

**생성:** 2026-03-25

---

## 섹션 1 — pg_textsearch 스케일링 비교 (1K/10K/100K)

### 인프라 컨텍스트

- **pg_textsearch BM25 AND**: `<@>` 연산자, BM25 인덱싱 (textsearch_ko MeCab)
- **pg_textsearch BM25 OR**: GIN 인덱스 + ts_rank_cd (OR matching)
- **VectorChord-BM25**: Block-WeakAnd posting list (Phase 6-3 실측)
- **pl/pgsql BM25 v2**: B-tree 역인덱스, real-TF (Phase 6-3 실측)

### 지연시간 (Latency)

#### p50 (50th percentile)

| 스케일 | BM25 AND | BM25+Dense RRF | BM25+Dense Bayes | BM25 OR | VectorChord | pl/pgsql |
|--------|----------|---------------|-----------------|---------|-------------|---------|
| 1K     | 0.40ms   | 0.73ms        | 0.73ms          | 1.05ms  | 1.10ms      | 2.31ms  |
| 10K    | 0.42ms   | 0.72ms        | 0.72ms          | 2.81ms  | 1.35ms      | 10.35ms |
| 100K   | 0.62ms   | 0.65ms        | 0.65ms          | 22.78ms | 3.58ms      | 85.58ms |

#### p95 (95th percentile)

| 스케일 | BM25 AND | BM25 OR | VectorChord | pl/pgsql |
|--------|----------|---------|-------------|---------|
| 1K     | 0.48ms   | 3.43ms  | 2.12ms      | 6.39ms  |
| 10K    | 0.60ms   | 21.93ms | 2.54ms      | 53.13ms |
| 100K   | 1.61ms   | 245.50ms| 7.18ms      | 403.71ms|

### 인덱스 구축 시간

| 스케일 | pg_textsearch BM25 | pg_textsearch GIN | VectorChord | pl/pgsql |
|--------|-------------------|------------------|------------|---------|
| 1K     | 0.15s              | 0.02s            | 0.25s      | 1.63s   |
| 10K    | 1.34s              | 0.12s            | 1.05s      | 15.43s  |
| 100K   | 12.27s             | 1.61s            | 1.20s      | 171.04s |

### 인덱스 크기

| 스케일 | pg_textsearch BM25 | pg_textsearch GIN | VectorChord | pl/pgsql |
|--------|-------------------|------------------|------------|---------|
| 1K     | 912 kB             | 1,024 kB         | 106 MB     | 5.9 MB  |
| 10K    | 4.1 MB             | 4.9 MB           | 395 MB     | 49 MB   |
| 100K   | 18 MB              | 23 MB            | 498 MB     | 501 MB  |

### 주요 발견사항

#### pg_textsearch BM25 AND: 모든 스케일에서 가장 빠름

- **1K**: 0.4ms p50 (1.1ms p95)
- **10K**: 0.42ms p50 (0.6ms p95)
- **100K**: 0.62ms p50 (1.61ms p95)

pg_textsearch는 가장 작은 인덱스 크기(912kB→18MB)를 유지하면서 일관되게 최고 성능을 제공합니다. 모든 스케일에서 0.4~0.62ms 범위로 안정적이며, p95도 1.61ms를 넘지 않습니다.

#### VectorChord-BM25: 1K/10K에서는 느리지만 100K에서 수용 가능

- **1K**: 1.1ms p50 (2.12ms p95)
- **10K**: 1.35ms p50 (2.54ms p95)
- **100K**: 3.58ms p50 (7.18ms p95)

VectorChord는 pg_textsearch보다 3~5배 느리지만, pl/pgsql 대비 24배 빠릅니다. 대규모 데이터셋에서는 pl/pgsql의 극심한 성능 저하(85.58ms)에 비해 합리적인 선택이지만, 인덱스 크기가 매우 큽니다(498MB).

#### pg_textsearch OR (GIN + ts_rank_cd): Recall 우선 시나리오에서만 사용

- **1K**: 1.05ms p50
- **10K**: 2.81ms p50
- **100K**: 22.78ms p50 (22배 증가)

pg_textsearch OR은 BM25 스코어링이 아니므로 정확도 중심 사용 사례에는 부적합합니다. Recall이 중요한 경우에만 사용하되, 100K에서는 지연시간이 심각해집니다.

#### pl/pgsql: 프로덕션 제외

- **1K**: 2.31ms p50
- **10K**: 10.35ms p50
- **100K**: 85.58ms p50 (37배 증가)

pl/pgsql 구현은 스케일링에 취약하며 100K에서 85ms를 초과합니다. 프로덕션 환경에서는 제외됩니다.

#### 인덱스 크기: pg_textsearch가 가장 효율적

- **100K에서**: pg_textsearch BM25 18MB vs VectorChord 498MB (27배 차이)
- **100K에서**: pg_textsearch GIN 23MB vs pl/pgsql 501MB (22배 차이)

pg_textsearch는 최소 저장 공간 요구량으로 최고 성능을 제공합니다.

---

## 섹션 2 — 하이브리드 검색 벤치마크

### 인프라 컨텍스트

- **어휘 기반(Lexical)**: textsearch_ko MeCab + pg_textsearch BM25 (`<@>` AND matching)
- **밀집(Dense)**: pgvector HNSW (BGE-M3 1024차원, m=16, ef_construction=200)
- **Fusion 방법**:
  - **RRF**: k=60, 각 컴포넌트 상위 60개 후 `1/(k+rank)` 합산
  - **Bayesian**: BM25 실제 스코어 + 코사인 유사도 정규화, α=0.5:0.5 결합

### MIRACL 벤치마크 (10K 문서)

#### 품질 메트릭

| 방법 | NDCG@10 | Recall@10 | MRR  |
|------|---------|-----------|------|
| BM25 | 0.6385  | 0.7974    | 0.617|
| Dense| 0.7904  | 0.9130    | 0.801|
| RRF  | 0.7683  | 0.8932    | 0.783|
| Bayesian | 0.7272  | 0.8706    | 0.722|

#### 지연시간 (ms)

| 방법 | p50  | p95  | p99  |
|------|------|------|------|
| BM25 | 0.44 | 0.60 | 0.74 |
| Dense| 1.20 | 1.58 | 1.79 |
| RRF  | 1.79 | 2.38 | 2.70 |
| Bayesian | 9.55 | 12.76 | 14.33 |

**분석**: 일반 위키 검색(MIRACL)에서는 밀집 검색이 최고 품질(NDCG 0.79)을 제공합니다. **RRF는 1.79ms에서 0.768 NDCG로 운영 최강 선택**입니다. Bayesian은 9.55ms의 오버헤드로 품질 이점이 없습니다.

### EZIS 벤치마크 (97개 기술 문서)

#### 품질 메트릭

| 방법 | NDCG@10 | Recall@10 | MRR  |
|------|---------|-----------|------|
| BM25 | 0.9162  | 0.9847    | 0.894|
| Dense| 0.8041  | 0.9351    | 0.762|
| RRF  | 0.8641  | 0.9695    | 0.829|
| Bayesian | 0.9249  | 0.9847    | 0.903|

#### 지연시간 (ms)

| 방법 | p50  | p95  | p99  |
|------|------|------|------|
| BM25 | 0.47 | 0.53 | 0.55 |
| Dense| 0.63 | 0.75 | 0.85 |
| RRF  | 0.92 | 1.09 | 1.35 |
| Bayesian | 13.87 | 15.01 | 15.27 |

**분석**: 기술 매뉴얼(EZIS)에서는 BM25가 지배적(NDCG 0.92)이며, Bayesian이 한계적으로 우수(0.925)하지만 **13.87ms 오버헤드**가 필요합니다. RRF는 0.92ms에서 0.864 NDCG로 합리적 선택입니다.

### 도메인별 역전 현상

| 도메인 | 최고 방법 | NDCG | 지연 | 특성 |
|--------|---------|------|------|------|
| MIRACL (일반 위키) | Dense | 0.79 | 1.20ms | 의미론적 임베딩 유리 |
| EZIS (기술 문서) | BM25 | 0.92 | 0.47ms | 정확한 용어 매칭 유리 |

**결론**: 같은 인프라가 도메인에 따라 반대 성능을 보입니다. 일반 텍스트는 밀집이 우수하고, 기술 문서는 BM25가 우수합니다.

### 각 Fusion 방법의 특성

#### RRF (Reciprocal Rank Fusion)

- **튜닝 불필요**: k=60 고정값으로 도메인 무관하게 동작
- **도메인 무관 견고함**: MIRACL 0.768, EZIS 0.864 모두 실용 수준
- **지연시간**: 1.79ms (MIRACL) / 0.92ms (EZIS) — DB 단일 라운드 트립
- **구현**: `p7_rrf_miracl`, `p7_rrf_ezis` SQL CTE 함수 (Python 병합 없음)

**추천**: 기본 하이브리드 전략. 튜닝 비용 없이 안정적인 품질.

#### Bayesian 융합

- **α 튜닝 필수**: 도메인별 BM25/Dense 가중치 조정 (α=0.5:0.5은 일반값)
- **BM25 스코어 추출 오버헤드**: `to_bm25query` 실제 점수 계산 (~10ms)
- **품질 이점**: BM25 강세 도메인(EZIS)에서만 한계적(+0.061 NDCG)
- **지연시간**: 9.55ms (MIRACL) / 13.87ms (EZIS) — RRF 대비 5~15배 높음
- **구현**: `p7_bayesian_miracl`, `p7_bayesian_ezis` SQL CTE 함수

**추천**: BM25가 명확히 지배하는 특정 도메인에서만 선택적 사용. 일반적으로 RRF 선호.

### DB 측 Fusion 구현

모든 병합은 PostgreSQL SQL CTE 함수로 수행됩니다:

```sql
p7_rrf_miracl(query_text, top_k)      -- RRF 기반 하이브리드
p7_rrf_ezis(query_text, top_k)        -- RRF 기반 하이브리드
p7_bayesian_miracl(query_text, top_k) -- Bayesian 기반 하이브리드
p7_bayesian_ezis(query_text, top_k)   -- Bayesian 기반 하이브리드
```

**특징**:
- Python 병합 없음 -> 네트워크 왕복 1회
- 병렬 BM25/Dense 쿼리 실행 가능
- 결과 정렬 및 점수 정규화 DB에서 처리

---

## 결론 및 권장 구성

### 최고의 PostgreSQL 하이브리드 인프라

#### 1. 어휘 기반 검색 (Lexical)

```
textsearch_ko (MeCab 기반 한글 토크나이저)
+ pg_textsearch BM25 `<@>` 연산자 (AND matching)
```

**선택 근거**:
- 모든 스케일(1K/10K/100K)에서 0.4~0.62ms 최고 성능
- 가장 작은 인덱스 크기(18MB @100K)
- VectorChord 대비 안정적이고 빠른 확장성

#### 2. 밀집 벡터 검색 (Dense)

```
pgvector HNSW
- 임베딩: BGE-M3 1024차원
- 파라미터: m=16, ef_construction=200
```

**선택 근거**:
- 일반 위키 검색(MIRACL)에서 최고 품질(NDCG 0.79)
- 안정적인 지연시간(1.2ms p50)

#### 3. 하이브리드 Fusion (기본)

```
RRF (Reciprocal Rank Fusion, k=60)
via SQL CTE 함수: p7_rrf_miracl, p7_rrf_ezis
```

**선택 근거**:
- 도메인 무관 견고한 성능(NDCG 0.768~0.864)
- 튜닝 불필요
- DB 단일 라운드 트립(1.79ms 이상)

#### 4. 하이브리드 Fusion (선택사항)

```
Bayesian (α=0.5:0.5)
via SQL CTE 함수: p7_bayesian_miracl, p7_bayesian_ezis
```

**사용 시점**:
- BM25가 도메인에서 명확히 우수한 경우(예: EZIS 기술 문서)
- 추가 지연(~13.87ms) 감수 가능 시
- α 값 미세 조정 가능 한 경우

### Phase 7 스케일링 검증

| 방법 | 1K | 10K | 100K | 확장성 | 프로덕션 적정 |
|------|-----|-----|------|--------|------------|
| pg_textsearch AND | [O] | [O] | [O] | 안정적(37x) | 우수 |
| VectorChord | [O] | [O] | [O] | 양호(3.2x) | 중간 |
| pl/pgsql | [O] | 위험 | [X] | 취약(37x) | 제외 |
| pg_textsearch OR | [O] | 주의 | 위험 | 취약(22x) | Recall만 |

### Phase 8 예정

- **pg_search (ParadeDB fork)**: 차세대 PostgreSQL 전문검색 비교
- **Meilisearch 통합**: 외부 검색 엔진 벤치마크
- **재순위 (Re-ranking)**: 쿼리당 재순위 지연 측정

---

## 기술 요약

**Phase 7은 pg_textsearch BM25를 PostgreSQL 전문검색의 표준으로 검증했습니다.**

- [O] 모든 스케일에서 최고 성능 (0.4~0.62ms)
- [O] 최소 인덱스 오버헤드 (18MB @100K)
- [O] 명확한 스케일링 경로 (1K->100K 단 1.5배 증가)
- [O] RRF 기반 도메인 무관 하이브리드 (NDCG 0.77+ 안정적)
- [O] DB 측 Fusion (Python 병합 제거, 네트워크 왕복 1회)

**다음 단계**: Phase 8에서 pg_search 포크 및 외부 검색 엔진과 비교하여 최종 아키텍처 확정.
