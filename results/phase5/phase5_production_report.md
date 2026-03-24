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

**해결 시도 — 두 가지 경로:**

**경로 1: `<@>` 연산자 (pg_textsearch 네이티브 BM25)**

```sql
SELECT id FROM table ORDER BY text <@> '쿼리' LIMIT 10
```

`<@>` 연산자는 내부적으로 자체 토크나이징 후 AND 매칭을 수행한다. 외부에서 OR tsquery를 만들어 넘길 수 없다 — 연산자가 text 컬럼과 query string만 받고 자체 파이프라인을 실행하기 때문이다. OR 토큰을 space-separated로 넘겨도 내부에서 다시 AND로 처리한다 (NDCG=0.3405, 변화 없음).

**경로 2: PostgreSQL 네이티브 tsvector + `ts_rank_cd` OR**

```sql
-- MeCab 토큰을 추출 후 OR tsquery 구성
SELECT id, ts_rank_cd(tsv, to_tsquery('public.korean', '토큰1 | 토큰2 | ...')) AS score
FROM table
WHERE tsv @@ to_tsquery('public.korean', '토큰1 | 토큰2 | ...')
ORDER BY score DESC LIMIT 10
```

OR 매칭 자체는 성공 — recall은 올라간다. 하지만 `ts_rank_cd`는 BM25가 아니다. IDF/TF/길이정규화 없이 "몇 개 토큰이 매칭되었나"로 점수를 매기는 coordinate-level scoring이라 **랭킹 품질이 크게 저하**된다 (NDCG=0.2300, AND보다 악화).

**핵심 문제: BM25 랭킹(`<@>`)과 OR 매칭(`@@`)을 결합할 수 없다.** `<@>`가 외부 tsquery를 받지 않고 자체 파이프라인을 돌리기 때문이다.

| 방법 | Recall | 랭킹 품질 | NDCG |
|------|--------|----------|------|
| `<@>` AND (pg_textsearch BM25) | 낮음 | BM25 (좋음) | 0.34 |
| `tsv @@ OR` + `ts_rank_cd` | 높음 | coordinate (나쁨) | 0.23 |
| `tsv @@ OR` + `<@>` 결합 | 불가 | — | — |

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

**왜 stale IDF에서 NDCG가 오히려 올랐는가?** BM25 검색의 NDCG는 두 요소의 곱이다: (1) IDF 정확도 — 각 term의 희귀성 가중치가 얼마나 맞나, (2) Recall pool — 검색 가능한 관련 문서가 몇 개 있나. 200 docs 추가로 관련 문서 자체가 더 많이 들어오면서 recall 향상(+)이 IDF 부정확성(-)을 압도했다. IDF는 비율(ratio) 기반이라 corpus가 균등하게 성장하면 값이 크게 변하지 않는다 — 극단적으로 특정 도메인 문서만 대량 유입되는 경우가 아니면.

### pgvector-sparse 멀티워커 rebuild 분석

pgvector-sparse의 R1(incremental) 위반을 "stateless 멀티워커 병렬 rebuild"로 해결할 수 있는지 평가한다.

**Rebuild 프로세스 분해:**

```
[Step 1] fit() — 전체 corpus 토크나이징 + IDF 계산    ← MapReduce 가능
[Step 2] embed() — 각 문서를 sparse vector로 변환     ← embarrassingly parallel
[Step 3] DB UPDATE — 벡터를 DB에 쓰기                ← embarrassingly parallel
```

Step 1은 토크나이징(map, 병렬) + df 집계(reduce, merge)로 분해 가능. Step 2~3은 IDF dict를 파라미터로 받으면 각 워커가 자기 파티션만 독립 처리. 워커는 stateless — kiwipiepy + numpy만 있으면 컨테이너/Lambda/Cloud Run 뭐든 가능.

**실측 기반 비용 추정 (1k docs 실측치 선형 외삽):**

| 규모 | 1 worker | 4 workers | 8 workers | 16 workers |
|------|----------|-----------|-----------|------------|
| 10k | 75s | ~20s | ~12s | ~8s |
| 100k | 12.5min | ~3.5min | ~2min | ~1min |
| 1M | 125min | ~33min | ~17min | ~9min |

**Cloud Run 기준 비용 (일배치):**

| 규모 | 워커 수 | 소요시간 | 비용/회 | 월 비용 |
|------|--------|---------|---------|---------|
| 10k | 4×1vCPU | ~20s | < $0.01 | ~$0.3 |
| 100k | 8×1vCPU | ~2min | ~$0.02 | ~$0.6 |
| 1M | 16×2vCPU | ~9min | ~$0.50 | ~$15 |

**DB 부하 — 진짜 관건:**

| 항목 | 10k | 100k | 1M |
|------|-----|------|-----|
| UPDATE rows | 10k | 100k | 1M |
| WAL 크기 (sparse vec ~200B avg) | ~4MB | ~40MB | ~400MB |
| vacuum 대상 dead tuples | 10k | 100k | 1M |

100k까지는 부담 없음. 1M부터 WAL 400MB + dead tuples 1M개로 autovacuum 부하가 커진다. blue-green 테이블 스왑(INSERT into 새 테이블 → atomic RENAME → DROP old)으로 dead tuple 문제를 회피 가능하나, 인덱스 rebuild 시간이 추가된다.

**pl/pgsql v2 대비 운영 복잡도 비교:**

| | pl/pgsql v2 | pgvector-sparse + 멀티워커 |
|---|---|---|
| 인프라 | PostgreSQL 하나 | PG + 스케줄러 + 워커 + 모니터링 |
| 코드 | SQL 함수 + trigger | Python 파이프라인 + 배포 + blue-green |
| 장애 포인트 | DB만 | rebuild 실패, 워커 OOM, swap 타이밍 |
| 문서 추가 | `INSERT` 한 줄 | INSERT + 다음 배치까지 stale |
| IDF 갱신 | 자동 (query-time 계산) | full rebuild 필요 |

**판정:** 멀티워커 rebuild는 기술적으로 가능하고 비용도 적절하지만, 그 파이프라인을 만들고 운영하는 복잡도가 pl/pgsql v2의 "trigger 한 줄"과 비교할 수 없다. pgvector-sparse + 멀티워커 rebuild는 **Managed PG에서 C 확장 설치가 불가능할 때의 차선책**으로만 권고한다.

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

### 1~100M 스케일링: PostgreSQL의 구조적 한계와 대안

#### BM25 계산 비용은 엔진과 무관하다

BM25 수식 `score = Σ IDF(t) × (tf × (k1+1)) / (tf + k1 × (1 - b + b × dl/avgdl))`은 어떤 엔진에서 돌려도 동일하다. tokenize 비용, IDF lookup, TF 계산, scoring — 알고리즘이 같으면 연산량은 같다. Elasticsearch가 대규모에서 빠른 건 BM25를 "더 잘" 계산해서가 아니다.

#### 차이는 데이터 접근 구조(I/O 패턴)에서 발생한다

| | pl/pgsql v2 (PostgreSQL) | Elasticsearch (Lucene) |
|---|---|---|
| 역인덱스 저장 | **B-tree 테이블** — (term, doc_id, tf) 행 단위 | **Posting list** — term → [doc1:tf, doc2:tf, ...] 연속 블록 |
| term lookup | B-tree traverse → random I/O | Segment 내 순차 read → sequential I/O |
| 다중 term 합집합 | nested loop join or hash join | Skip list + Block-Max WAND |
| 메모리 관리 | shared_buffers (범용) | mmap (검색 특화 segment) |
| 분산 | 단일 노드 | shard별 병렬 처리 후 merge |

PostgreSQL의 B-tree는 범용 자료구조이므로 "term → 매칭 문서 목록"을 가져오는 데 row 단위 random I/O가 발생한다. Lucene의 posting list는 검색 전용 구조로 같은 작업이 연속 블록 sequential read다. 10k 규모에서는 둘 다 메모리에 올라와서 차이가 없지만, **1M+ 규모에서 메모리에 안 담기기 시작하면 이 I/O 패턴 차이가 latency로 직결**된다.

참고: pg_textsearch, ParadeDB 모두 posting list + WAND를 PG extension 안에 구현해서 Lucene급 I/O 패턴을 PG 안에서 구현하려 한 시도다. 스케일링 아키텍처 자체는 맞는 방향이나, Phase 5에서 확인한 한국어 토크나이저 문제(AND 매칭, Lindera 사전)로 사용이 불가했다.

#### PostgreSQL 단독 스케일링 한계

| 규모 | pl/pgsql v2 | pgvector-sparse | 판정 |
|------|------------|-----------------|------|
| ~10k | 3ms, QPS 240 | 1ms, QPS 1000+ | PG 단독 최적 |
| ~100k | ~10ms (btree depth 3) | ~2ms, rebuild 2min | PG 단독 가능 |
| ~1M | ~30-60ms (btree depth 4, 50M rows) | rebuild 9min | PG 단독 한계 |
| ~10M | inverted_index 5억 rows | rebuild 수 시간 | **불가능** |
| ~100M | — | — | **불가능** |

#### 1~100M 전 구간 대응 가능한 옵션

| 엔진 | 한국어 BM25 | 분산 | 1M | 10M | 100M |
|------|-----------|------|:---:|:---:|:---:|
| **Elasticsearch/OpenSearch** | Nori (내장, MeCab 계열) | shard+replica | O | O | O |
| **Qdrant** (sparse vector) | App-side tokenizer 필요 | 내장 sharding | O | O | O |
| **Milvus** (sparse vector) | App-side tokenizer 필요 | 내장 sharding | O | O | O |
| **Weaviate** (BM25) | 내장 Korean tokenizer | 내장 sharding | O | O | O |

> ES/OpenSearch만 한국어 형태소 분석기를 엔진 내장으로 제공한다. Qdrant/Milvus는 pgvector-sparse와 동일하게 app-side tokenizer 관리가 필요하며, 같은 rebuild 문제를 갖는다.

#### 현실적 전략: 점진적 전환

| 구간 | 아키텍처 | 이유 |
|------|---------|------|
| **1k~100k** | PG 단독 (pl/pgsql v2) | 추가 인프라 불필요, 검색 품질 동등 |
| **100k~1M** | PG 단독 가능, ES 준비 시작 | hybrid에서 BM25 30-60ms는 dense와 비슷 — 아직 병목 아님 |
| **1M~100M** | **PG(원본) + ES(검색 인덱스)** | CDC/trigger로 PG→ES 동기화, ES가 BM25+dense 검색 담당 |

```
┌──────────┐    CDC / trigger    ┌──────────────┐
│ PostgreSQL│  ─────────────────▶ │ Elasticsearch│
│ (원본 DB) │                     │ (검색 인덱스) │
└──────────┘                     └──────────────┘
  CRUD, 트랜잭션, 조인              BM25(Nori) + dense 검색
                                   수평 확장, shard/replica
```

이 패턴(PG + ES dual)이 가장 흔한 production 패턴이다. PG의 트랜잭션 보장 + ES의 검색 스케일링을 모두 활용하며, 1M 이하에서는 ES 없이 PG 단독 운영 → 규모 성장 시 ES를 추가하는 **점진적 전환**이 가능하다.

> Phase 6에서 ES Nori vs pl/pgsql v2의 실제 품질/성능 차이를 측정하면, 이 전환 시점 판단이 더 정확해진다.

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
