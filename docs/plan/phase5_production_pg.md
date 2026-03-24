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

| 세팅 ID | 방법 | MIRACL NDCG@10 | EZIS NDCG@10 | 비고 |
|---------|------|---------------|-------------|------|
| **5-sparse** | pgvector-sparse BM25 (kiwi-cong) | 0.6326 | 0.9455 | Python-side 토크나이즈 |
| **5-pgsql** | pl/pgsql BM25 + MeCab | 0.6412 | 0.9290 | 완전 DB-side |
| **5-hybrid** | Bayesian BM25+BGE-M3 dense | 0.7476 | 0.9493 | 두 인덱스 동시 관리 |

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

| 세팅 | 추가 비용 | IDF staleness 여부 |
|------|---------|--------------------|
| 5-sparse | kiwi-cong 토크나이즈 → sparse vector upsert | **있음** — corpus 통계 변경 시 이전 벡터 무효 |
| 5-pgsql | bm25idx INSERT + bm25df UPDATE + bm25stats UPDATE | **있음** — IDF 재계산 범위에 따라 근사 vs 전체 재빌드 |
| 5-hybrid | sparse upsert + BGE-M3 임베딩 + dense upsert | sparse는 staleness 있음, dense는 corpus 독립 |

**핵심 문제**: BM25 IDF는 corpus 전체 통계 기반. 문서 추가 시 IDF가 바뀌면 이전 문서들의 BM25 가중치도 무효화됨.

- pgvector-sparse: 새 문서 추가는 O(1)이지만 기존 벡터들은 구 IDF 기준 — staleness 누적
- pl/pgsql: bm25stats 업데이트는 incremental 가능하나, bm25idx의 기존 tfidf 값은 stale
- 실험: 초기 10k 인덱싱 후 1k 추가, NDCG 재측정 → staleness 실제 영향 정량화

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

### 4. HNSW 인덱스 효과 (하이브리드 내 dense 컴포넌트)

하이브리드(5-hybrid)에서 dense 벡터 검색 부분에 HNSW 적용 시 효과 측정.

| 인덱스 | dense 쿼리 latency (p50) | recall 손실 |
|--------|------------------------|------------|
| exact scan (현재) | ~120ms (DB 부분만) | 0% |
| HNSW m=16, ef=64 | ? | 예상 <2% |
| HNSW m=16, ef=200 | ? | 예상 <1% |

> sparse 벡터는 pgvector에서 HNSW 미지원 — exact scan 유일 옵션.

---

## 실험 매트릭스

| 실험 ID | 세팅 | 규모 | 측정 항목 |
|---------|------|------|---------|
| 5-A | pgvector-sparse kiwi-cong | 10k | 오프라인 빌드, 온라인 추가 latency, QPS@1/4/8/16, IDF staleness |
| 5-B | pl/pgsql BM25 + MeCab | 10k | 오프라인 빌드, 온라인 추가 latency, QPS@1/4/8/16, IDF staleness |
| 5-C | Bayesian BM25+BGE-M3 dense (exact) | 10k | 오프라인 빌드, 온라인 추가 latency (dual-index), QPS@1/4/8 |
| 5-D | Bayesian BM25+BGE-M3 dense + HNSW | 10k | dense HNSW 빌드 비용, latency 개선, recall tradeoff |
| 5-E | (선택) 100k scale | 100k | 5-A/B/C 동일 측정, 스케일링 곡선 |

---

## 핵심 가설 및 검증 포인트

1. **IDF staleness 정량화**: 초기 10k 인덱싱 후 1k 추가 시 NDCG 저하 측정.
   pgvector-sparse vs pl/pgsql — 어느 쪽이 더 빨리 degradation되는가?

2. **Concurrent 병목**: kiwi Python-side 토크나이즈가 8-concurrent 환경에서 병목인가?
   pl/pgsql MeCab DB-side 대비 QPS 차이가 실질적인가?

3. **Hybrid 운영 비용 정당화**: BM25+dense 하이브리드는 두 인덱스 관리 비용이 있음.
   EZIS NDCG 0.9493 vs BM25 단독 0.9455의 +0.4% 개선이 ~2x 운영 비용을 정당화하는가?

4. **HNSW 가성비**: 하이브리드 dense 컴포넌트에 HNSW 적용 시 latency 개선 vs 인덱스 빌드 비용.

---

## 출력

- `results/phase5/phase5_production_pg.json` — 실험별 측정 결과
- `results/phase5/phase5_production_report.md` — 세팅별 운영 적합성 평가
- `experiments/phase5_production/phase5_production_bench.py` — 벤치마크 스크립트
