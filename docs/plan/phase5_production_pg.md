# Phase 5: Production PostgreSQL BM25/Hybrid 최적 세팅

## 목표

"지속적으로 문서가 추가되는 프로덕션 환경"에서 최적의 PostgreSQL 한국어 검색 세팅을 찾는다.

단순 검색 품질(NDCG)뿐 아니라 **인덱스 구축 비용**, **쿼리 latency/throughput**, **온라인 문서 추가 비용**을 함께 고려한다.

핵심 질문: "pgvector-sparse + kiwi-cong vs BGE-M3 dense — 실제 운영 환경에서 어떤 게 맞는가?"

## 의존성

- Phase 3 완료 (pgvector-sparse BM25 kiwi-cong, NDCG=0.6326)
- Phase 4 완료 (BGE-M3 dense NDCG=0.7915, Bayesian BM25+dense NDCG EZIS=0.9493)

---

## 평가 차원

### 1. 검색 품질 (기존 측정 완료)

| 세팅 | MIRACL NDCG@10 | EZIS NDCG@10 |
|------|---------------|-------------|
| pgvector-sparse BM25 (kiwi-cong) | 0.6326 | 0.9455 |
| pl/pgsql BM25 + MeCab | 0.6412 | 0.9290 |
| BGE-M3 dense | 0.7915 | 0.8060 |
| Bayesian BM25+BGE-M3 dense | 0.7476 | **0.9493** |

### 2. 오프라인 인덱스 구축 비용 (신규 측정)

| 세팅 | 측정 항목 |
|------|---------|
| pgvector-sparse kiwi-cong | 토크나이징 throughput (docs/s), embedding 시간, pgvector upsert 시간 |
| BGE-M3 dense | 임베딩 throughput (docs/s, batch=32/64), pgvector upsert 시간 |
| pl/pgsql BM25 | pl/pgsql 빌드 함수 실행 시간 (10k docs) |

규모별 측정: 1k / 10k / 100k docs

### 3. 온라인 문서 추가 비용 (지속적 추가 시나리오)

가정: 실시간으로 문서가 1개씩 추가될 때 (배치 아님)

| 세팅 | 추가 비용 |
|------|---------|
| pgvector-sparse kiwi-cong | 단일 문서 토크나이즈 → sparse vector upsert (O(1)) |
| BGE-M3 dense | 단일 문서 임베딩 (~253ms 추론) → dense vector upsert (O(1)) |
| pl/pgsql BM25 | bm25idx INSERT + bm25df UPDATE + bm25stats UPDATE — IDF 재계산 필요 여부 |

**핵심 문제**: BM25 IDF는 corpus 전체 통계 기반. 문서 추가 시 IDF가 바뀌면 이전 문서들의 BM25 가중치도 무효화됨 → 전체 재인덱싱 필요?

pgvector-sparse BM25는 이 문제가 있음. dense embedding은 corpus 독립적 → 추가 시 해당 문서만 임베딩.

### 4. 쿼리 Latency / Throughput

| 세팅 | 현재 측정 (p50) | 미측정 |
|------|----------------|--------|
| pgvector-sparse kiwi-cong | 4ms | concurrent QPS |
| BGE-M3 dense | 253ms (임베딩 포함) | concurrent QPS |
| pl/pgsql BM25 + MeCab | 10ms | concurrent QPS |

**신규 측정**: concurrent 요청 시 throughput (QPS @ 1/4/8/16 concurrent)

- pgvector-sparse: kiwi Python 토크나이즈 GIL 이슈 가능
- BGE-M3 dense: 임베딩 모델이 bottleneck, MPS 병렬화 한계
- pl/pgsql: 완전 DB-side, connection pool로 horizontal scale 가능

### 5. 인덱스 자료구조 및 쿼리 속도

| 세팅 | 인덱스 | 효과 |
|------|--------|------|
| pgvector-sparse | 현재: exact scan (no index) | HNSW 미지원(sparse). IVF 없음. 선형 스캔 |
| pgvector-dense | **HNSW 가능** (ivfflat / hnsw) | 100k docs에서 latency 대폭 개선, recall 약간 손실 |
| pl/pgsql BM25 | GIN index on bm25idx(term) | term lookup O(log n) |

**신규 측정**: HNSW 인덱스 유무에 따른 BGE-M3 dense 쿼리 latency 비교 (10k / 100k docs)

---

## 실험 매트릭스

| 실험 ID | 세팅 | 규모 | 측정 |
|---------|------|------|------|
| 5-A | pgvector-sparse kiwi-cong (exact) | 10k | 오프라인 빌드, 온라인 추가, QPS@1/4/8 |
| 5-B | BGE-M3 dense (no index) | 10k | 오프라인 빌드, 온라인 추가, QPS@1/4/8 |
| 5-C | BGE-M3 dense + HNSW (m=16, ef=64) | 10k | 빌드, 쿼리, recall vs latency tradeoff |
| 5-D | Bayesian BM25+BGE-M3 dense | 10k | 하이브리드 온라인 추가 비용 (두 인덱스 동시 관리) |
| 5-E | pl/pgsql BM25 + MeCab | 10k | IDF 재계산 정책 (문서 추가 시 전체 재빌드 vs 근사) |
| 5-F | (선택) 100k scale extrapolation | 100k | 5-A/B/C 동일 측정, 스케일링 곡선 |

---

## 핵심 가설 및 검증 포인트

1. **IDF staleness**: pgvector-sparse BM25는 문서 추가 시 corpus 통계가 바뀌어 이전 벡터들이 부정확해짐. 실제로 얼마나 NDCG가 저하되는가? (추가 100개 후 재측정)

2. **Dense HNSW 가성비**: HNSW(m=16) 적용 시 latency가 253ms → ?ms로 줄어드는가? recall 손실은?

3. **Concurrent throughput**: kiwi Python-side 토크나이즈가 8 concurrent 환경에서 병목이 되는가? pl/pgsql 대비 차이?

4. **Hybrid 운영 비용**: Bayesian 하이브리드는 BM25 벡터 + dense 벡터 두 개를 관리해야 함. 문서 추가 시 두 인덱스 모두 갱신 필요 — 실제 latency?

---

## 출력

- `results/phase5/phase5_production_pg.json` — 실험별 측정 결과
- `results/phase5/phase5_production_report.md` — 세팅별 운영 적합성 평가
- `experiments/phase5_production/phase5_production_bench.py` — 신규 실험 스크립트
