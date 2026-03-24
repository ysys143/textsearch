# Phase 5: Production PostgreSQL BM25/Hybrid 최적 세팅

## 목표

"지속적으로 문서가 추가되는 프로덕션 환경"에서 최적의 PostgreSQL 한국어 BM25/Hybrid 검색 세팅을 찾는다.

단순 검색 품질(NDCG)뿐 아니라 **인덱스 구축 비용**, **쿼리 latency/throughput**, **온라인 문서 추가 비용**을 함께 고려한다.

> **전제**: Hybrid(BM25+dense)는 필수 — Phase 4에서 EZIS 0.9493(hybrid) vs 0.8060(dense 단독)으로 확인됨.
> Dense(BGE-M3)가 표준, BM25는 정확도를 올리는 보조 신호.
> Phase 5의 질문은 hybrid 채택 여부가 아니라, **hybrid 내 BM25 컴포넌트의 최적 구성**.

### Production 요구사항

| # | 요구사항 | 설명 |
|---|---------|------|
| R1 | **Incremental update** | 문서 추가 시 full rebuild 없이 인덱스 갱신 |
| R2 | **App에서 tokenizer/사전 관리 불필요** | 형태소 분석기와 단어사전을 어플리케이션이 유지보수하지 않음 |
| R3 | **DB-managed reverse index** | document 테이블 INSERT → DB가 reverse index를 자동 관리 (pgvector HNSW와 동일 패턴) |
| R4 | **Document-index 일관성** | DB 레벨에서 문서와 인덱스 간 일관성 보장 |
| R5 | **기성 솔루션 우선** | 가능하면 확장 설치만으로 해결, pl/pgsql 직접 작성은 폴백 |

핵심 질문: "위 요구사항을 모두 충족하는 BM25 구성이 존재하는가?
pg_textsearch(기성)가 OR-query 개선으로 recall을 회복할 수 있는가? 불가능하면 pl/pgsql(직접 구현)이 production-ready한 수준인가?"

## 의존성

- Phase 3 완료 (pgvector-sparse BM25 kiwi-cong NDCG=0.6326, pl/pgsql+MeCab NDCG=0.6412)
- Phase 4 완료 (Bayesian BM25+BGE-M3 dense NDCG EZIS=0.9493, MIRACL=0.7476)

---

## 평가 대상 세팅

| 세팅 ID | 방법 | MIRACL NDCG@10 | EZIS NDCG@10 | IDF 구조 | 비고 |
|---------|------|---------------|-------------|---------|------|
| **5-sparse** | pgvector-sparse BM25 (kiwi-cong) | 0.6326 | 0.9455 | **pre-computed** (벡터에 내장) | Python-side 토크나이즈, incremental 불가 |
| **5-pgsql** | pl/pgsql BM25 + MeCab | 0.6412 | 0.9290 | **query-time** (실시간 계산) | 완전 DB-side, incremental 가능 |
| **5-ts** | pg_textsearch + MeCab (OR-query 개선) | 0.3374 (Phase 2 AND 기준) | 0.8417 | WAND 내장 | **개선 실험**: AND→OR 쿼리 변환으로 recall 회복 시도 |
| **5-hybrid** | Bayesian BM25+BGE-M3 dense | 0.7476 | 0.9493 | BM25 컴포넌트에 따라 결정 | **최종 목표** — BM25 컴포넌트를 위 3가지 중 최적으로 선택 |

### 구조적 차이: IDF 계산 시점

두 BM25 구현은 IDF를 다루는 방식이 근본적으로 다르다:

**pgvector-sparse (pre-computed IDF)**:
- `BM25Embedder_PG.fit()` 시점에 corpus 통계(df, avgdl, N) 계산
- 각 문서의 sparse vector에 IDF가 TF와 함께 곱해져 저장됨
- 문서 추가 시: 새 벡터 INSERT는 O(1), 그러나 기존 벡터는 구 IDF 기준 → **full rebuild 필요**
- 관리 부담: pip install kiwipiepy만으로 완결 (managed PG 호환)

**pl/pgsql (query-time IDF)**:
- `inverted_index`에는 `term_freq`과 `doc_length`만 저장 (TF 정보만)
- `bm25_ranking()` 함수가 쿼리마다 `AVG(doc_length)`, `COUNT(DISTINCT doc_id)`, `COUNT(DISTINCT doc_id) AS df`를 실시간 계산
- 문서 추가 시: trigger로 `inverted_index` INSERT만 하면 끝 → **incremental update 자연스러움**, IDF staleness 없음
- 관리 부담: textsearch_ko C 확장 + MeCab 바이너리 (self-hosted PG 전용, managed PG 불가)

> **현재 구현의 문제**: `bm25_ranking()`이 매 쿼리마다 `AVG(doc_length)`, `COUNT(DISTINCT doc_id)`를 inverted_index에서 full scan으로 집계.
> 10k docs에서 10ms이지만 100k+ 규모에서는 선형 증가 예상 — production 부적합.
>
> **최적화 방향**: corpus 통계를 별도 테이블(`bm25_stats`, `bm25_df`)로 분리, 문서 추가 시 trigger로 incremental update.
> 쿼리 시 stats lookup O(1) + term index lookup O(log n) → full scan 제거, query-time IDF 장점은 유지.

---

## 평가 차원

### 1. 오프라인 인덱스 구축 비용

규모별 측정: **1k / 10k / 100k docs**

| 세팅 | 측정 항목 |
|------|---------|
| 5-sparse | kiwi-cong 토크나이징 throughput (docs/s) + pgvector upsert 시간 |
| 5-pgsql | pl/pgsql bm25_build() 실행 시간 (전체 재빌드 기준) |
| 5-hybrid | sparse 빌드 + BGE-M3 임베딩 throughput (batch=32/64) + dense upsert 시간 |

### 2. 온라인 문서 추가 비용 (실시간 1건씩 추가)

| 세팅 | 추가 방법 | IDF staleness |
|------|---------|---------------|
| 5-sparse | kiwi-cong 토크나이즈 → sparse vector upsert | **full rebuild 필요** — IDF가 벡터에 내장, 기존 벡터 전체 무효 |
| 5-pgsql | trigger로 inverted_index INSERT (자동) | **없음** — IDF를 쿼리 시점에 실시간 계산 |
| 5-hybrid | (BM25 컴포넌트 방식에 따름) + BGE-M3 임베딩 + dense upsert | BM25 부분은 위와 동일, dense는 corpus 독립 |

이 차이가 Phase 5의 핵심 tradeoff:
- **5-sparse**: 빠른 검색(4ms) + 높은 갱신 비용(full rebuild)
- **5-pgsql**: 느린 검색(10ms) + 낮은 갱신 비용(incremental INSERT)

**실험**: 5-sparse에서 초기 10k 인덱싱 후 1k 추가(rebuild 없이), NDCG 재측정 → staleness 실제 영향 정량화.
rebuild 주기(매 100건? 500건? 1000건?)에 따른 NDCG 유지 곡선 측정.

### 3. 쿼리 Latency / Concurrent Throughput

| 세팅 | 현재 p50 | 신규 측정 |
|------|---------|---------|
| 5-sparse | 4ms | QPS @ 1/4/8/16 concurrent |
| 5-pgsql | 10ms | QPS @ 1/4/8/16 concurrent |
| 5-hybrid | 379ms (Bayesian) | QPS @ 1/4/8 concurrent |

병목 예상:
- **5-sparse**: Python-side kiwi 토크나이즈 → GIL로 인한 concurrent 병목 가능
- **5-pgsql**: 완전 DB-side → connection pool로 horizontal scale 가능, concurrent 유리
- **5-hybrid**: BGE-M3 MPS 추론이 bottleneck, concurrent 시 큐잉 발생 예상

---

## 실험 매트릭스

| 실험 ID | 세팅 | 규모 | 측정 항목 |
|---------|------|------|---------|
| 5-A | pgvector-sparse kiwi-cong | 10k | 오프라인 빌드, 온라인 추가 latency, QPS@1/4/8/16, IDF staleness |
| 5-B1 | pl/pgsql BM25 + MeCab (현재: full scan) | 10k | 오프라인 빌드, 온라인 추가 latency, QPS@1/4/8/16 |
| 5-B2 | pl/pgsql BM25 + MeCab (최적화: stats 테이블 분리) | 10k | full scan 제거 후 latency/QPS 개선 측정, 스케일링 곡선 |
| 5-T | pg_textsearch + MeCab (AND→OR 쿼리 개선) | 10k | NDCG/R@10 재측정, latency, QPS — 0.86ms 유지되면서 recall 회복하는가? |
| 5-C | Bayesian BM25+BGE-M3 dense | 10k | 오프라인 빌드, 온라인 추가 latency (dual-index), QPS@1/4/8 |
| 5-D | (선택) 100k scale | 100k | 5-A/B/C/T 동일 측정, 스케일링 곡선 |

---

## 핵심 가설 및 검증 포인트

1. **IDF staleness 정량화 (5-sparse 전용)**: 초기 10k 인덱싱 후 rebuild 없이 1k 추가 시 NDCG 저하 측정.
   pl/pgsql은 query-time IDF이므로 staleness 없음 — 5-sparse만 해당.
   rebuild 주기별(100/500/1000건) NDCG 유지 곡선으로 최적 rebuild 정책 도출.

2. **Concurrent 병목**: kiwi Python-side 토크나이즈가 8-concurrent 환경에서 병목인가?
   pl/pgsql MeCab DB-side 대비 QPS 차이가 실질적인가?

3. **pg_textsearch OR-query 개선 (5-T)**: Phase 2에서 pg_textsearch + MeCab이 NDCG=0.3374, R@10=0.3844로 부진했던 원인은
   `<@>` 연산자가 내부적으로 AND 매칭(plainto_tsquery 유사)하여 관련 문서 대부분을 탈락시킨 것.
   OR tsquery를 직접 구성(`to_tsquery('term1 | term2 | ...')`)하여 recall을 회복하면서 WAND의 sub-ms latency를 유지할 수 있는가?
   성공 시: **0.86ms latency + 높은 recall** — production에서 가장 이상적인 조합 가능.

4. **Hybrid BM25 컴포넌트 최적 선택**: hybrid는 전제. 각 BM25 구현의 hybrid 내 성능 비교:
   - 5-sparse: 빠른 검색 but full rebuild 필요 → hybrid 파이프라인에서 rebuild 주기가 운영에 미치는 영향?
   - 5-B2: incremental update 가능 but latency 높음 → dense 쿼리 시간(~120ms)과 합산 시 BM25 latency가 차지하는 비중은?
   - 5-T: sub-ms latency + incremental → hybrid에서 BM25 latency를 무시할 수 있는 수준인가?

---

## 핵심 산출물: Production 요구사항 적합성 평가표

실험 완료 후 아래 표를 실측치 기반으로 채워 최종 판단한다.

| 요구사항 | pgvector-sparse (5-A) | pl/pgsql+MeCab (5-B) | pg_textsearch+MeCab (5-T) |
|---------|----------------------|---------------------|--------------------------|
| R1 Incremental update | ? (staleness 정량화) | ? (trigger 검증) | ? (USING bm25 자동) |
| R2 App tokenizer 불필요 | ? (kiwi Python-side) | ? (DB-side MeCab) | ? (DB-side MeCab) |
| R3 DB-managed index | ? (app이 벡터 계산) | ? (trigger 기반) | ? (확장이 관리) |
| R4 Document-index 일관성 | ? | ? | ? |
| R5 기성 솔루션 | ? | ? (직접 구현) | ? (확장 설치) |
| NDCG@10 (MIRACL) | 0.6326 | 0.6412 | ? (OR-query 후 재측정) |
| Latency p50 | 4ms | ? (stats 분리 후) | ? (OR-query 후) |
| QPS@8 concurrent | ? | ? | ? |
| Hybrid 통합 latency | ? | ? | ? |

→ 요구사항 R1~R5 전부 충족 + NDCG/latency 최선인 세팅을 **Phase 6 baseline BM25 컴포넌트**로 확정.

## 판단 시나리오

- **5-T 성공** (pg_textsearch OR-query로 R@10 > 0.7): pg_textsearch 채택 — 기성 솔루션 + sub-ms + incremental
- **5-T 실패** (recall 회복 불가): pl/pgsql+MeCab(5-B2) 채택 — 직접 구현이지만 R1~R4 충족
- **5-B2도 부적합** (latency/scale 문제): pgvector-sparse + 정기 rebuild 정책으로 타협

---

## 보고서 구성

`results/phase5/phase5_production_report.md`에 아래 내용을 포함한다.

### Part 1: 기성 솔루션의 한국어 실패 분석

pg_textsearch(Timescale)와 pg_bm25(ParadeDB)는 "production-grade BM25"를 표방하고 등장했다.
그러나 한국어 환경에서 두 솔루션 모두 pl/pgsql 직접 구현(NDCG=0.6412) 대비 처참한 결과를 보였다.

| 솔루션 | 표방 | MIRACL NDCG@10 | 실패 원인 |
|--------|------|---------------|---------|
| pg_textsearch + MeCab | BM25/WAND, sub-ms | 0.3374 | ? (AND 매칭? tsvector 정보 손실? WAND 공격적 pruning?) |
| ParadeDB pg_search | Tantivy BM25 | 0.2275 | ? (korean_lindera 품질? 커스텀 토크나이저 불가?) |

각 솔루션별로:
- **근본 원인 분석**: 코드 레벨에서 왜 실패하는지 (쿼리 매칭 방식, 토크나이저 아키텍처, 인덱스 구조)
- **해결 가능성**: 어떤 수정으로 개선할 수 있는지 (OR-query, 파라미터 튜닝, 소스 수정 등)
- **해결된 솔루션의 성능**: 수정 후 NDCG/latency 실측
- **유지보수 가능성**: 수정이 upstream 업데이트에 안전한가, 포크 유지가 필요한가

### Part 2: 요구사항 적합성 평가표

위 "핵심 산출물" 섹션의 R1~R5 비교표를 실측치로 완성.

### Part 3: pl/pgsql BM25 스케일링 분석

pl/pgsql 직접 구현이 폴백이라면, TB급 프로덕션 환경에서 실제로 사용 가능한지 검토.

| 검토 항목 | 분석 내용 |
|----------|---------|
| **inverted_index 테이블 크기** | 10k/100k/1M docs 기준 행 수 및 디스크 사용량 추정 |
| **GIN/btree 인덱스 효율** | term lookup O(log n) — 1M, 10M terms에서 실제 latency |
| **stats 테이블 분리 후 쿼리 복잡도** | full scan 제거 시 쿼리 플랜 분석 (EXPLAIN ANALYZE) |
| **concurrent 성능** | connection pool 기반 QPS@16/32/64 — DB CPU 병목 지점 |
| **파티셔닝 가능성** | inverted_index를 term range 기반 파티셔닝 시 이점 |
| **materialized view / BRIN** | 대규모에서 corpus 통계 캐싱 전략 |
| **비교 대상** | Elasticsearch 단일 노드의 동일 규모 성능과 대비 |

### Part 4: 최종 권고

실험 결과 + 스케일링 분석을 종합하여 production 환경별 권고안 도출.

| 환경 | 권고 BM25 구성 | 이유 |
|------|--------------|------|
| Managed PG (RDS, Cloud SQL) | ? | textsearch_ko 설치 불가 시 대안 포함 |
| Self-hosted PG (10k~100k docs) | ? | |
| Self-hosted PG (1M+ docs, TB급) | ? | 스케일링 한계 도달 시 ES 권고 여부 포함 |

---

## 출력

- `results/phase5/phase5_production_pg.json` — 실험별 측정 결과
- `results/phase5/phase5_production_report.md` — 위 Part 1~4 포함 종합 보고서
- `experiments/phase5_production/phase5_production_bench.py` — 벤치마크 스크립트
