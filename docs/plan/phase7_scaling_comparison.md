# Phase 7: PostgreSQL 스케일링 비교

## 목적

Phase 6-3에서 VectorChord vs pl/pgsql BM25 v2 스케일링을 측정했지만 pg_textsearch(GIN)가 빠졌음.
Phase 7은 세 가지 PostgreSQL 검색 방법을 **동일 조건**에서 1K/10K/100K 규모별로 비교해
프로덕션 선택 기준을 확립한다.

**핵심 질문**: 100K docs 규모에서 latency/정확도/관리 비용을 고려할 때 무엇을 써야 하는가?

---

## 비교 대상

| 방법 | 랭킹 알고리즘 | 인덱스 타입 | Phase 6-3 포함 |
|------|-------------|------------|---------------|
| pg_textsearch AND (`<@>`) | ts_rank (TF-IDF 근사) | GIN | 미포함 |
| pg_textsearch OR+WAND (`<@>`) | ts_rank | GIN+WAND | 미포함 |
| VectorChord-BM25 | BM25 (Block-WeakAnd) | bm25vector | [O] |
| pl/pgsql BM25 v2 | BM25 (real-TF) | B-tree inverted | [O] |

---

## 측정 항목

| 항목 | 설명 |
|------|------|
| Insert throughput | docs/sec (tokenize + index write) |
| Index build time | 초 |
| Index size on disk | MB |
| Query latency p50/p95 | ms (213 MIRACL queries) |
| NDCG@10 | 10K corpus 기준 (Phase 5/6 실측값 재사용) |

---

## 예상 결과

Phase 5 실측(10K) + Phase 6-3 실측(100K) 기반 예측:

| Scale | pg_textsearch AND | VectorChord | pl/pgsql |
|-------|------------------|-------------|----------|
| 10K p50 | **0.49ms** | 1.35ms | 10.35ms |
| 100K p50 | ~1-3ms (예상) | 3.58ms | 85.58ms |
| NDCG@10 | 0.6401 | **0.6415** | 0.6414 |

pg_textsearch AND가 100K에서도 가장 빠를 가능성이 높으나,
랭킹 품질은 BM25 계열보다 0.0014 낮음.

---

## 구현

Phase 6-3 스크립트(`phase6_3_scaling.py`)에 pg_textsearch 섹션 추가:

```python
def run_pgsearch_scale(conn_main, queries, scale):
    table = f"docs_scale_{scale // 1000}k"
    # CREATE INDEX ... USING GIN (to_tsvector('public.korean', text))
    # OR: functional index on pre-computed tsvector column
    # Query: SELECT id FROM {table}
    #        ORDER BY ts_rank(tsvec, plainto_tsquery('public.korean', %s)) DESC
    #        LIMIT 10
```

---

## 출력

- `results/phase7/phase7_scaling_report.md`
- `results/phase7/phase7_scaling.json`
