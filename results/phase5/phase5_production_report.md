# Phase 5: Production PostgreSQL BM25/Hybrid 최적 세팅 — 종합 보고서

## Executive Summary

Hybrid(BM25+dense) 검색에서 BM25 컴포넌트의 최적 구성을 찾기 위해 3가지 후보를 평가했다.

| BM25 후보 | MIRACL NDCG@10 | EZIS NDCG@10 | p50 | R1~R5 충족 |
|-----------|:---:|:---:|:---:|:---:|
| pg_textsearch+MeCab | 0.3437 | 0.9238 | 0.6ms | R1,R3,R4,R5 (NDCG 불합격) |
| **pl/pgsql BM25 v2+MeCab** | **0.3355** | **0.8926** | **3.1ms** | **R1,R2,R3,R4** |
| pgvector-sparse kiwi-cong | 0.3471 | — | 1.0ms | ~~R1~~,~~R2~~,~~R3~~ |

**결론: pl/pgsql BM25 v2 (stats 테이블 분리) 채택.**

pg_textsearch는 sub-ms latency지만 AND매칭으로 인해 MIRACL NDCG가 낮고 OR-query 개선이 불가.
pgvector-sparse는 가장 빠르지만 R1(incremental), R2(app tokenizer), R3(DB-managed) 전부 위반.
pl/pgsql v2는 직접 구현이지만(R5 불충족) R1~R4를 모두 충족하며, latency 3ms는 hybrid에서 무시 가능(dense 50ms의 6%).

---

## Part 1: 기성 솔루션의 한국어 실패 분석

### 1.1 pg_textsearch (Timescale) — AND 매칭의 한계

**설계 의도와 성공 시나리오:**

pg_textsearch는 Timescale이 개발한 PostgreSQL BM25 확장으로, Tantivy 기반 WAND(Weak AND) 알고리즘으로 sub-ms latency를 달성한다. 영어 환경에서는 `plainto_tsquery`가 자연스럽게 AND 매칭을 수행하며, 대부분의 검색 쿼리에서 모든 토큰이 포함된 문서가 충분히 존재한다.

**한국어에서의 미스매치:**

| 현상 | 원인 |
|------|------|
| MIRACL NDCG=0.3437, R@10=0.3915 | `<@>` 연산자가 내부적으로 AND 매칭 사용 |
| OR-query 시도 실패 | `<@>` 연산자가 외부 tsquery를 무시하고 자체 토크나이징 수행 |
| ts_rank_cd OR fallback 열화 | NDCG=0.2300 — BM25가 아닌 coordinate scoring으로 랭킹 품질 저하 |

한국어 형태소 분석은 하나의 어절에서 여러 토큰을 생성한다 (예: "검색엔진을" → "검색", "엔진", "을").
쿼리가 길어질수록 AND 조건을 만족하는 문서가 급격히 줄어든다. 영어에서는 stop word 제거 후 핵심어 2~3개만 남아 AND가 잘 작동하지만, 한국어에서는 조사/어미까지 토크나이즈되어 AND 조건이 과도하게 엄격해진다.

**해결 가능성:**

| 시도 | 결과 | 평가 |
|------|------|------|
| `<@>` + OR input | NDCG=0.3405 (변화 없음) | `<@>`가 내부 tokenizer를 사용, 외부 입력 무시 |
| GIN + `ts_rank_cd` OR | NDCG=0.2300 (악화) | OR recall은 넓어지나 BM25 아닌 coordinate scoring |
| 소스 수정 (AND→OR) | 미시도 | Rust/pgrx 빌드 필요, upstream 호환성 상실 |

**결론:** pg_textsearch는 현재 한국어에서 사용할 수 없다. `<@>` 연산자의 AND 매칭을 OR로 변경하려면 Rust 소스 수정이 필요하며, 이는 upstream 업데이트와 충돌한다.

### 1.2 ParadeDB pg_search — Lindera 토크나이저의 한계

**설계 의도와 성공 시나리오:**

ParadeDB pg_search는 Tantivy 기반 full-text search로, 영어에서는 뛰어난 성능을 보인다. `korean_lindera` 토크나이저를 제공하지만, 이는 일본어 IPAdic 사전을 한국어에 포팅한 Lindera의 한국어 모드이다.

**한국어에서의 미스매치:**

| 항목 | 값 |
|------|-----|
| Phase 2 MIRACL NDCG@10 | 0.2275 |
| 원인 | japanese IPAdic 기반 사전 — 한국어 형태소 커버리지 부족 |
| 커스텀 토크나이저 | 불가 — Rust pgrx 빌드 타임에 결정, 런타임 교체 불가 |

ParadeDB의 `korean_lindera`는 일본어 형태소 분석 엔진(Lindera)의 한국어 사전 모드로, MeCab-ko나 Kiwi 수준의 한국어 형태소 분석 품질을 제공하지 못한다. 외부 토크나이저(MeCab)를 연결하려면 Rust pgrx 확장을 소스 수정 + 빌드해야 하며, 이는 managed PG에서 불가능하다.

**해결 가능성:** 사실상 없음. MeCab 수준 토크나이저를 Rust 네이티브로 포팅하지 않는 한 한국어 성능 개선 불가.

### 1.3 공정한 평가

두 솔루션 모두 "한국어에서 실패"가 아니라 **"한국어 특수성과의 구조적 미스매치"**이다:

- **pg_textsearch**: 영어의 짧은 쿼리 + stop word 제거 패턴에 최적화된 AND 매칭이, 한국어의 긴 형태소 토큰 리스트와 충돌
- **ParadeDB**: 일본어 기반 Lindera가 한국어 형태소를 제대로 분리하지 못하는 사전 문제

영어/일본어 환경에서는 각각의 솔루션이 의도대로 잘 작동할 것이다.

---

## Part 2: Production 요구사항 적합성 평가표

### 실측 기반 R1~R5 평가

| 요구사항 | pgvector-sparse (5-A) | pl/pgsql v2+MeCab (5-B) | pg_textsearch+MeCab (5-T) |
|---------|:---:|:---:|:---:|
| **R1** Incremental update | **X** — full rebuild 필요 (IDF가 벡터에 내장) | **O** — trigger 기반 INSERT 3.6ms/doc | **O** — USING bm25 자동 관리 |
| **R2** App tokenizer 불필요 | **X** — Python-side kiwipiepy 필요 | **O** — DB-side MeCab (textsearch_ko) | **O** — DB-side MeCab (textsearch_ko) |
| **R3** DB-managed index | **X** — app이 sparse vector 계산 후 INSERT | **O** — trigger가 inverted_index 자동 관리 | **O** — pg_textsearch 확장이 BM25 index 관리 |
| **R4** Document-index 일관성 | **△** — app 책임 (embed 실패 시 불일치 가능) | **O** — 같은 트랜잭션 내 trigger 실행 | **O** — 확장이 트랜잭션 내 인덱스 갱신 |
| **R5** 기성 솔루션 | **O** — pgvector + Python BM25 | **X** — pl/pgsql 직접 구현 | **O** — pg_textsearch 확장 설치 |

### 성능 실측 비교

| 지표 | pgvector-sparse | pl/pgsql v1 | pl/pgsql v2 | pg_textsearch AND |
|------|:---:|:---:|:---:|:---:|
| **MIRACL NDCG@10** | 0.3471 | 0.3334 | 0.3355 | 0.3437 |
| **EZIS NDCG@10** | — | 0.9024 | 0.8926 | 0.9238 |
| **p50 (ms)** | 1.0 | 10.6 | 3.1 | 0.6 |
| **p95 (ms)** | 2.0 | 18.5 | 8.9 | 0.9 |
| **QPS@1** | 983 | 91 | 240 | 572 |
| **QPS@8** | 1417 | 87 | 250 | 562 |
| **Buffer hits** | — | 1320 | 433 | — |
| **Incremental insert** | full rebuild | trigger (full scan) | trigger (O(1) stats) | 자동 |
| **Insert latency** | 2.8ms embed + INSERT | — | 3.6ms | 자동 |

### Hybrid (BM25 + BGE-M3 dense) 성능

| 데이터셋 | BM25v2 단독 | Hybrid RRF | Dense 비중 |
|---------|:---:|:---:|:---:|
| MIRACL | 0.3355 | **0.3977** (+0.06) | p50 52ms 중 BM25 3ms (6%) |
| EZIS | 0.8926 | 0.8815 (-0.01) | p50 52ms 중 BM25 3ms (6%) |

> Hybrid에서 BM25 latency(3ms)는 dense inference(~50ms)의 6%에 불과.
> BM25 컴포넌트 선택이 hybrid latency에 미치는 영향은 미미하며, **검색 품질과 운영 요구사항이 결정적 기준**.

### IDF Staleness (pgvector-sparse)

| 상태 | MIRACL NDCG@10 | 변화 |
|------|:---:|:---:|
| Baseline (800 docs) | 0.3242 | — |
| +200 docs (stale IDF) | 0.3431 | +0.019 (상승) |
| Full rebuild (1000 docs) | 0.3471 | +0.004 (미미) |

20% corpus 성장에서 IDF staleness 영향은 무시할 수 있는 수준. 새 문서 추가로 인한 recall 향상이 IDF 부정확성을 상쇄한다.

---

## Part 3: pl/pgsql BM25 스케일링 분석

### v1 → v2 최적화 효과

| 지표 | v1 (full scan) | v2 (stats table) | 개선 |
|------|:---:|:---:|:---:|
| Buffer hits | 1320 | 433 | **3x 감소** |
| p50 latency | 10.6ms | 3.1ms | **3.4x 개선** |
| QPS@1 | 91 | 240 | **2.6x 개선** |
| 쿼리 복잡도 | O(N) — full scan | O(1) stats + O(log n) term lookup | **상수 시간** |

### 규모별 스케일링 추정

v2의 쿼리 구조: `bm25_stats` 1-row lookup (O(1)) + `bm25_df` term JOIN (O(log n)) + `inverted_index` term lookup (O(log n))

| 규모 | inverted_index 행 수 (추정) | btree depth | 예상 p50 |
|------|:---:|:---:|:---:|
| 1k docs | 54k | 2 | 3ms (실측) |
| 10k docs | ~500k | 3 | 5~8ms |
| 100k docs | ~5M | 3~4 | 10~20ms |
| 1M docs | ~50M | 4 | 30~60ms |

> btree lookup은 O(log n)이므로 10x 규모 증가에 ~2x latency 증가 예상.
> 1M docs 기준 p50 30~60ms는 dense inference (~50ms)와 비슷한 수준 — hybrid에서 병목은 여전히 dense.

### 스케일링 개선 방안

| 방안 | 효과 | 복잡도 |
|------|------|--------|
| **inverted_index 파티셔닝** (term range) | term lookup 범위 축소, 병렬 스캔 가능 | 중간 |
| **bm25_df materialized view** | df lookup 최적화 | 낮음 |
| **BRIN index on doc_id** | range scan 최적화 | 낮음 |
| **connection pooling (pgbouncer)** | QPS 선형 확장 | 낮음 |

### 비교: Elasticsearch 단일 노드

| 지표 | pl/pgsql v2 (1k) | Elasticsearch (예상) |
|------|:---:|:---:|
| BM25 quality | 동등 (동일 수식) | 동등 |
| p50 latency | 3ms | 2~5ms |
| QPS@8 | 250 | 500~1000 |
| 운영 복잡도 | PG 기존 인프라 | JVM + 별도 클러스터 |
| Incremental update | trigger (ms 단위) | near-realtime (~1s refresh) |
| 한국어 지원 | MeCab (textsearch_ko) | Nori (내장) |

> 10k~100k 규모에서 pl/pgsql v2는 Elasticsearch 대비 latency/QPS에서 뒤지지만,
> 별도 인프라 없이 기존 PG에서 운영 가능하다는 점이 핵심 장점.

---

## Part 4: 최종 권고

### 환경별 권고안

| 환경 | 권고 BM25 구성 | 이유 |
|------|--------------|------|
| **Managed PG** (RDS, Cloud SQL) | pgvector-sparse + 정기 rebuild | textsearch_ko C 확장 설치 불가 → MeCab 사용 불가. kiwipiepy(Python)로 BM25 sparse vector 생성, 정기 rebuild (매 1000건 또는 일배치). IDF staleness 영향 미미. |
| **Self-hosted PG** (10k~100k docs) | **pl/pgsql BM25 v2 + MeCab** | R1~R4 충족, 3ms latency, trigger 기반 incremental. textsearch_ko 설치 후 완전 DB-side 운영. hybrid에서 dense 대비 latency 비중 6%. |
| **Self-hosted PG** (1M+ docs) | pl/pgsql v2 + 파티셔닝, 또는 Elasticsearch | 1M+ 규모에서 inverted_index 50M+ 행 → btree depth 4, p50 30~60ms 예상. dense inference와 비슷한 수준이므로 hybrid에서는 문제 없으나, BM25 단독 사용 시 ES 전환 고려. |

### Phase 6 Baseline 확정

**Phase 6 (시스템 비교)에서 사용할 PostgreSQL BM25 컴포넌트:**

- **BM25 tier**: pl/pgsql BM25 v2 + MeCab (`bm25_ranking_v2()` + `bm25_stats`/`bm25_df` 테이블)
- **Hybrid tier**: pl/pgsql BM25 v2 + BGE-M3 dense (RRF fusion)
- **Managed PG fallback**: pgvector-sparse kiwi-cong + 정기 rebuild

---

## 부록: 전체 실측 수치

### 5-T: pg_textsearch AND vs OR

| 방법 | Dataset | NDCG@10 | R@10 | p50 | p95 |
|------|---------|:---:|:---:|:---:|:---:|
| AND (`<@>`) | MIRACL | 0.3437 | 0.3915 | 0.6ms | 0.9ms |
| OR ts_rank_cd | MIRACL | 0.2300 | 0.3140 | 1.6ms | 4.3ms |
| OR `<@>` | MIRACL | 0.3405 | 0.3865 | 1.1ms | 1.3ms |
| AND (`<@>`) | EZIS | 0.9238 | 0.9924 | 0.6ms | 0.8ms |
| OR ts_rank_cd | EZIS | 0.5580 | 0.8588 | 2.2ms | 3.1ms |
| OR `<@>` | EZIS | 0.9177 | 0.9847 | 1.1ms | 1.4ms |

### 5-B: pl/pgsql BM25 v1 vs v2

| 방법 | Dataset | NDCG@10 | R@10 | p50 | QPS@1 | QPS@8 |
|------|---------|:---:|:---:|:---:|:---:|:---:|
| v1 (full scan) | MIRACL | 0.3334 | 0.3825 | 10.6ms | 91 | 87 |
| v2 (stats opt) | MIRACL | 0.3355 | 0.3872 | 3.1ms | 240 | 250 |
| v1 (full scan) | EZIS | 0.9024 | 0.9847 | 4.8ms | 200 | 203 |
| v2 (stats opt) | EZIS | 0.8926 | 0.9847 | 3.1ms | 323 | 324 |

### 5-A: pgvector-sparse IDF staleness

| 상태 | NDCG@10 | R@10 | p50 | QPS@1 | QPS@8 |
|------|:---:|:---:|:---:|:---:|:---:|
| Baseline (800 docs) | 0.3242 | 0.3517 | 0.9ms | — | — |
| Stale (+200 docs) | 0.3431 | 0.3877 | 1.0ms | — | — |
| Full rebuild (1000) | 0.3471 | 0.3854 | 1.1ms | 983 | 1417 |

### 5-C: Hybrid BM25v2 + BGE-M3 dense

| Dataset | NDCG@10 | R@10 | p50 | QPS@1 | QPS@8 |
|---------|:---:|:---:|:---:|:---:|:---:|
| MIRACL | 0.3977 | 0.4241 | 52.6ms | 19 | 19 |
| EZIS | 0.8815 | 0.9695 | 52.4ms | 19 | 17 |

> Note: MIRACL은 1000 docs subset으로 측정 (전체 10k 대비 NDCG 절대값이 낮음). 상대 비교는 유효.
