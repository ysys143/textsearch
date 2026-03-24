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

## 향후 Phase

| Phase | 주제 | 상태 |
|-------|------|------|
| Phase 6 | VectorChord-BM25 + textsearch_ko | next |
| Phase 7 | pg_textsearch 포크 | planned |
| Phase 8 | pg_search 포크 | planned |
| Phase 9 | 시스템 비교 (ES/Qdrant/Weaviate) | planned |
