# 실험 결과 요약

Phase 0~5 완료 결과. 각 Phase의 핵심 질문, 답, 데이터 기반 근거를 정리한다.

## Phase 인덱스

| Phase | 주제 | 핵심 질문 | 상태 | 결과 |
|-------|------|-----------|------|------|
| **Phase 0** | 데이터 준비 | MIRACL-ko + EZIS QA set 구축 | done | (데이터 전처리, 별도 결과 없음) |
| **Phase 1** | [형태소 분석기 비교](phase1_morphological_analyzers.md) | 어떤 한국어 형태소 분석기가 가장 좋은가? | done | MeCab (textsearch_ko) 1위 |
| **Phase 2** | [tsvector 한국어 통합](phase2_tsvector_korean.md) | PG FTS에 한국어를 통합하는 최선의 방법은? | done | textsearch_ko `public.korean` config |
| **Phase 3** | [PostgreSQL Native BM25](phase3_native_bm25.md) | PG 안에서 BM25를 구현하는 최선의 방법은? | done | pl/pgsql + pgvector-sparse 양강 |
| **Phase 4** | [BM25 vs Neural Sparse](phase4_bm25_vs_neural.md) | BM25가 neural sparse를 이길 수 있는가? | done | 데이터 성격에 따라 역전, hybrid 최강 |
| **Phase 5** | [Production PG 최적 세팅](phase5_production_pg.md) | Production 환경 최적 BM25 구성은? | done | pl/pgsql v2 + BGE-M3 hybrid |
| **Phase 6** | [VectorChord-BM25 + pg_tokenizer 스케일링](phase6/README.md) | VectorChord가 pl/pgsql보다 빠른가? | done | VectorChord 우세 |
| **Phase 7** | [PostgreSQL 최선 스택 확정](phase7_pg_best_stack.md) | textsearch_ko 스케일링 최적화와 하이브리드 방법론? | done | textsearch_ko + RRF = 최선 |
| **Phase 8** | [외부 시스템 비교](phase8_system_comparison.md) | PostgreSQL 최선 스택 vs ES / Qdrant 1.15.x / Vespa — 동등 조건 비교 | done | PG 스택 유지 권장 |

## 데이터셋

| 데이터셋 | 성격 | 쿼리 수 | 코퍼스 |
|---------|------|---------|--------|
| MIRACL-ko | 일반 Wikipedia | 213 | 10k passages |
| EZIS Oracle Manual QA | 도메인 특화 기술 매뉴얼 | ~120 | ~200 chunks |

## Phase 1~5 핵심 수치 요약

### BM25-only 티어 (Phase 3 확정)

| 방법 | MIRACL NDCG@10 | EZIS NDCG@10 | p50 latency |
|------|---------------|-------------|-------------|
| **pl/pgsql BM25 + MeCab** | **0.6412** | 0.9290 | 10ms |
| pgvector-sparse (kiwi-cong) | 0.6326 | **0.9455** | 4ms |
| pg_textsearch + MeCab (AND) | 0.3374 | 0.8488 | 0.86ms |
| pg_search korean_lindera | 0.2348 | - | 2ms |

### Neural 티어 (Phase 4 확정)

| 방법 | MIRACL NDCG@10 | EZIS NDCG@10 | p50 latency |
|------|---------------|-------------|-------------|
| **BGE-M3 dense** | **0.7915** | 0.8060 | 253ms |
| Bayesian BM25+BGE-M3 | 0.7476 | **0.9493** | 379ms |
| splade-ko sparse | 0.6962 | 0.8998 | 105ms |

### Production 권장 (Phase 5 확정)

- **BM25**: pl/pgsql v2 (stats 테이블 분리, trigger 기반 incremental)
- **Hybrid**: Bayesian BM25 + BGE-M3 dense fusion
- **스케일링**: PG scale-up으로 ~100M docs까지 가능, 그 이후 ES 전환 검토

### VectorChord-BM25 스케일링 (Phase 6 확정)

| 방법 | 1K docs p50 | 10K docs p50 | 100K docs p50 | 우위 |
|------|-----------|-----------|-----------|------|
| **VectorChord-BM25** | **1.1ms** | **1.35ms** | **3.58ms** | VectorChord 우세 |
| pl/pgsql BM25 | 2.31ms | 10.35ms | 85.58ms | - |

### pg_textsearch 스케일링 & 하이브리드 (Phase 7 확정)

**1. textsearch_ko 스케일링**
- pg_textsearch BM25 AND: p50 = 0.4ms (1K) / 0.42ms (10K) / 0.62ms (100K)
- **전 스케일 최속** — VectorChord, pl/pgsql 모두 압도

**2. 하이브리드 벤치마크**

| 방법 | 데이터셋 | NDCG@10 | p50 latency | 평가 |
|------|---------|---------|-----------|------|
| **RRF** (BM25 + Dense) | MIRACL | **0.77** | 1.79ms | 실용적 선택 |
| Dense | MIRACL | 0.79 | 2.35ms | 최고 정확도 |
| **BM25** | EZIS | **0.92** | 0.4ms | 도메인 강세 |
| Bayesian | EZIS | 0.93 | 13.87ms | 느린 대신 미미한 향상 |

**3. 최선 PostgreSQL 하이브리드 스택**
- **인덱싱**: textsearch_ko + pgvector HNSW
- **BM25**: pg_textsearch (AND 쿼리)
- **Dense**: BGE-M3 또는 유사 임베딩
- **Fusion**: DB-side RRF (Reciprocal Rank Fusion)
- **결론**: 속도와 정확도의 균형을 잘 맞춘 구성

## Phase 7 최종 확정 스택

```
textsearch_ko (MeCab 형태소) + pg_textsearch BM25 (`<@>` AND)
+ pgvector HNSW (BGE-M3 1024-dim, m=16, ef_construction=200)
+ DB-side RRF SQL CTE (k=60, topk=60)
```

- MIRACL: BM25=0.6385, Dense=0.7904, RRF=0.7683 @1.79ms p50
- EZIS:   BM25=0.9162, Dense=0.8041, RRF=0.8641 @0.92ms p50

## Phase 8 외부 시스템 비교 결과

| 시스템 | MIRACL Hybrid NDCG | EZIS Hybrid NDCG | Hybrid p50 | 핵심 한계 |
|--------|-------------------|-----------------|-----------|---------|
| **PostgreSQL** | **0.7683** | 0.8641 | **1.79ms** | — |
| ES 8.17 (nori) | 0.7501 | **0.8769** | 5.18ms | Trial 라이선스 필요 |
| Qdrant 1.15 | 0.6924 | 0.8394 | 4.54ms | Self-hosted BM25 없음 |
| Vespa 8.663 | 0.4463 | 0.8125 | 4.14ms | ICU 비형태소 |

상세: [phase8_system_comparison.md](phase8_system_comparison.md) | [ES](phase8_compare_es.md) | [Qdrant](phase8_compare_qdrant.md) | [Vespa](phase8_compare_vespa.md)
