# Phase 5: 시스템 비교

## 목표

Phase 2~4에서 찾은 PostgreSQL 최선 세팅을 전문 검색 엔진들과 비교한다.
동일 corpus, 동일 쿼리로 검색 품질 + 데이터 스케일별 latency & throughput 측정.

"PostgreSQL이 Elasticsearch/Qdrant를 실제로 대체할 수 있는가?"

## 의존성

- Phase 0~4 전체 완료
- Phase 2~4 최선 세팅 확정

---

## 실험 대상 시스템

| 시스템 | Korean 설정 | Docker profile | 포트 |
|--------|------------|----------------|------|
| **PostgreSQL + pg_bm25** | Phase 3 최선 (ParadeDB Korean tokenizer) | `core` | 5432 |
| **PostgreSQL + tsvector** | Phase 2 최선 Korean config | `core` | 5432 |
| **PostgreSQL + pgvector sparse** | Phase 4 최선 (BM25 or neural) | `core` | 5432 |
| **Elasticsearch** | `nori` analyzer (내장 Korean 형태소, MeCab 계열) | `phase5-es` | 9200 |
| **Qdrant** | sparse BM25 vector (동일 tokenizer) | `phase5-qdrant` | 6333 |
| **Vespa** | Korean linguistic processing | `phase5-vespa` | 8080 |

각 시스템은 독립 실행 (동시 실행 X — fair latency 비교를 위해 profile 분리).

---

## Elasticsearch 설정

```json
PUT /docs_ko
{
  "settings": {
    "analysis": {
      "analyzer": {
        "korean": {
          "type": "nori",
          "decompound_mode": "mixed",
          "stoptags": ["E", "IC", "J", "MAG", "MM", "SP", "SSC", "SSO", "SC", "SE", "XPN", "XSA", "XSN", "XSV", "UNA", "NA", "VSV"]
        }
      }
    }
  },
  "mappings": {
    "properties": {
      "text": { "type": "text", "analyzer": "korean" }
    }
  }
}
```

BM25 파라미터: Elasticsearch 기본 (k1=1.2, b=0.75)

---

## Qdrant 설정

```python
from qdrant_client import QdrantClient
from qdrant_client.models import SparseVectorParams, Distance

client.create_collection(
    "docs_ko",
    sparse_vectors_config={
        "bm25": SparseVectorParams(modifier="idf")
    }
)
```

동일 tokenizer(Phase 1 top-1)로 생성한 sparse BM25 벡터 사용.

---

## 실험 매트릭스

### 데이터 규모별

| 규모 | 문서 수 | 쿼리 수 |
|------|--------|--------|
| Small | 1,000 | 50 |
| Medium | 10,000 | 213 (MIRACL full) |
| Large (선택) | 100,000 | 213 |

### 측정 항목

| 항목 | 설명 |
|------|------|
| NDCG@10 | 검색 품질 |
| Recall@10 | 재현율 |
| 인덱스 빌드 시간 | 전체 corpus 인덱싱 |
| latency p50/p95/p99 | 쿼리당 응답 시간 (warm cache) |
| QPS | 초당 처리 쿼리 수 (동시 요청 없음, sequential) |

두 데이터셋(MIRACL + EZIS) 각각 측정.

---

## 핵심 가설

| 시나리오 | 예상 |
|---------|------|
| 검색 품질 (MIRACL) | ES ≥ PostgreSQL+pg_bm25 > PostgreSQL+tsvector |
| 검색 품질 (EZIS) | PostgreSQL+pg_bm25 ≈ ES > neural |
| 인덱스 빌드 속도 | PostgreSQL > ES (ES는 JVM warm-up 오버헤드) |
| 쿼리 latency (1k) | PostgreSQL < ES < Qdrant (small corpus, PG 유리) |
| 쿼리 latency (100k) | ES ≈ Qdrant < PostgreSQL (대규모, 전문 엔진 유리) |
| 운영 복잡도 | PostgreSQL << ES < Qdrant (PG는 기존 인프라 재사용) |

---

## 출력

- `results/phase5/phase5_final_report.md` — Phase 1~4 전체 통합 결과표
- `results/phase5/phase5_summary.json`

> **비고**: Elasticsearch/Qdrant/Vespa Docker 이미지 pull 실패로 Phase 5 외부 시스템 비교는 미실시.
> Phase 5는 Phase 1~4 PostgreSQL-only 결과 통합 리포트로 대체.

---

## 실험 결과 요약

### MIRACL-ko 최종 순위 (상위 10)

| Phase | 방법 | NDCG@10 | Latency p50 |
|-------|------|---------|-------------|
| phase4 | BGE-M3 dense (cosine) | **0.7915** | 253ms |
| phase4 | BGE-M3 sparse (neural) | 0.7634 | 157ms |
| phase4 | Hybrid BM25+BGE-M3 dense (RRF) | 0.7527 | 641ms |
| phase4 | Bayesian BM25+BGE-M3 sparse | 0.7485 | 291ms |
| phase4 | Bayesian BM25+BGE-M3 dense | 0.7476 | 379ms |
| phase4 | Hybrid BM25+BGE-M3 sparse (RRF) | 0.7160 | 119ms |
| phase4 | splade-ko (yjoonjang/splade-ko-v1) | 0.6962 | **104.67ms** |
| phase2 | pl/pgsql BM25 + MeCab (public.korean) | 0.6412 | 10ms |
| phase3 | pgvector-sparse BM25 (kiwi-cong) | 0.6326 | 4ms |

### EZIS 최종 순위 (상위 5)

| Phase | 방법 | NDCG@10 |
|-------|------|---------|
| phase4 | Bayesian BM25+BGE-M3 dense | **0.9493** |
| phase1/3/4 | BM25 kiwi-cong | 0.9455 |
| phase4 | Bayesian BM25+BGE-M3 sparse | 0.9394 |
| phase2 | pl/pgsql BM25 + MeCab | 0.9290 |
| phase4 | splade-ko | 0.8998 |

### 핵심 발견

1. **MIRACL**: BGE-M3 dense 단독 (NDCG=0.7915)이 어떤 hybrid도 능가
2. **EZIS**: BM25 kiwi-cong (0.9455)이 dense (0.8060)를 크게 앞서는 도메인 특성 (전문 기술 용어)
3. **splade-ko 재측정**: pgvector sparsevec DB-side scan → p50 555ms→**104.67ms** (5.3×), 병목은 MPS 모델 추론 (~100ms)
4. **pl/pgsql 4-table BM25**: 인덱스 재설계(bm25idx+bm25df+bm25doclen+bm25stats)로 p50 45ms→10ms (4.3×)
5. **PostgreSQL vs 전문 엔진**: BM25 품질 기준 pl/pgsql+MeCab (0.6412)이 pgvector-sparse (0.6326)를 능가; neural은 별도 GPU 추론 필요

### Production 추천

| 시나리오 | 추천 |
|---------|------|
| 품질 최우선 (GPU 있음) | BGE-M3 dense 단독 |
| 품질+비용 균형 | Bayesian BM25+BGE-M3 dense |
| PostgreSQL-only, 저지연 | pl/pgsql BM25 + MeCab (public.korean) |
| 기술 도메인 문서 | BM25 kiwi-cong 단독 |
