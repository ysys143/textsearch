# Phase 5: Production PostgreSQL BM25/Hybrid 최적 세팅

## 목표

"지속적으로 문서가 추가되는 프로덕션 환경"에서 최적의 PostgreSQL 한국어 BM25/Hybrid 검색 세팅을 찾는다.

단순 검색 품질(NDCG)뿐 아니라 **인덱스 구축 비용**, **쿼리 latency/throughput**, **온라인 문서 추가 비용**을 함께 고려한다.

> **Dense 단독 제외**: BGE-M3 dense 단독은 Phase 4에서 이미 측정 완료(NDCG=0.7915).
> Phase 5는 BM25 기반 세팅 및 BM25+dense 하이브리드의 **운영 비용 최적화**에 집중한다.

핵심 질문: "pgvector-sparse kiwi-cong vs pl/pgsql BM25+MeCab — 운영 환경에서 어느 것이 맞는가?
그리고 하이브리드(BM25+dense)를 선택할 경우 추가 운영 비용은 정당화되는가?"

## 의존성

- Phase 3 완료 (pgvector-sparse BM25 kiwi-cong NDCG=0.6326, pl/pgsql+MeCab NDCG=0.6412)
- Phase 4 완료 (Bayesian BM25+BGE-M3 dense NDCG EZIS=0.9493, MIRACL=0.7476)

---

## 평가 대상 세팅

| 세팅 ID | 방법 | MIRACL NDCG@10 | EZIS NDCG@10 | IDF 구조 | 비고 |
|---------|------|---------------|-------------|---------|------|
| **5-sparse** | pgvector-sparse BM25 (kiwi-cong) | 0.6326 | 0.9455 | **pre-computed** (벡터에 내장) | Python-side 토크나이즈, incremental 불가 |
| **5-pgsql** | pl/pgsql BM25 + MeCab | 0.6412 | 0.9290 | **query-time** (실시간 계산) | 완전 DB-side, incremental 가능 |
| **5-hybrid** | Bayesian BM25+BGE-M3 dense | 0.7476 | 0.9493 | BM25 컴포넌트에 따라 결정 | 두 인덱스 동시 관리 |

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
| 5-B | pl/pgsql BM25 + MeCab | 10k | 오프라인 빌드, 온라인 추가 latency, QPS@1/4/8/16, IDF staleness |
| 5-C | Bayesian BM25+BGE-M3 dense | 10k | 오프라인 빌드, 온라인 추가 latency (dual-index), QPS@1/4/8 |
| 5-D | (선택) 100k scale | 100k | 5-A/B/C 동일 측정, 스케일링 곡선 |

---

## 핵심 가설 및 검증 포인트

1. **IDF staleness 정량화 (5-sparse 전용)**: 초기 10k 인덱싱 후 rebuild 없이 1k 추가 시 NDCG 저하 측정.
   pl/pgsql은 query-time IDF이므로 staleness 없음 — 5-sparse만 해당.
   rebuild 주기별(100/500/1000건) NDCG 유지 곡선으로 최적 rebuild 정책 도출.

2. **Concurrent 병목**: kiwi Python-side 토크나이즈가 8-concurrent 환경에서 병목인가?
   pl/pgsql MeCab DB-side 대비 QPS 차이가 실질적인가?

3. **Hybrid 운영 비용 정당화**: BM25+dense 하이브리드는 두 인덱스 관리 비용이 있음.
   EZIS NDCG 0.9493 vs BM25 단독 0.9455의 +0.4% 개선이 ~2x 운영 비용을 정당화하는가?

---

## 출력

- `results/phase5/phase5_production_pg.json` — 실험별 측정 결과
- `results/phase5/phase5_production_report.md` — 세팅별 운영 적합성 평가
- `experiments/phase5_production/phase5_production_bench.py` — 벤치마크 스크립트
