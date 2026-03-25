# Korean Text Search Benchmark — 전체 실험 계획

## 연구 목표

한국어 텍스트 검색에서 PostgreSQL이 Elasticsearch를 대체할 수 있는가?
각 레이어(형태소 분석기, FTS 통합, BM25 구현, 검색 엔진)별로 최선의 방법을 실험으로 검증한다.

## 데이터셋

| 데이터셋 | 성격 | 쿼리 수 | 코퍼스 |
|---------|------|---------|--------|
| MIRACL-ko | 일반 Wikipedia, neural 유리 예상 | 213 | 10k passages |
| EZIS Oracle Manual QA | 도메인 특화 기술 매뉴얼, BM25 유리 예상 | ~120 | ~200 chunks |

두 데이터셋을 모든 phase에서 병행 평가 — 데이터 성격에 따른 방법론 역전 현상 확인이 핵심 인사이트.

## 평가 지표

- **검색 품질**: NDCG@10, Recall@10, MRR
- **속도**: 인덱스 빌드 시간, 쿼리 latency p50/p95/p99, QPS
- **기타**: vocab size, 토크나이징 throughput (docs/s)

## Phase 구성

| Phase | 주제 | 핵심 질문 | 상태 | 문서 |
|-------|------|-----------|------|------|
| **Phase 0** | 데이터 준비 | MIRACL-ko 로드 + EZIS PDF → QA set 생성 | [done] | [phase0_data_prep.md](phase0_data_prep.md) |
| **Phase 1** | 형태소 분석기 비교 | 어떤 한국어 형태소 분석기가 가장 좋은가? | [done] | [phase1_morphological_analyzers.md](phase1_morphological_analyzers.md) |
| **Phase 2** | tsvector 한국어 통합 | PostgreSQL 네이티브 FTS에 한국어 형태소를 통합하는 최선의 방법은? | [done] | [phase2_tsvector_korean.md](phase2_tsvector_korean.md) |
| **Phase 3** | PostgreSQL Native BM25 | PostgreSQL 안에서 BM25를 구현하는 최선의 방법은? | [done] | [phase3_native_bm25.md](phase3_native_bm25.md) |
| **Phase 4** | BM25 vs Neural Sparse | 형태소+BM25 조합이 neural sparse를 이길 수 있는가? | [done] | [phase4_bm25_vs_neural.md](phase4_bm25_vs_neural.md) |
| **Phase 5** | Production PG 최적 세팅 | 지속적 문서 추가 환경에서 latency/throughput/비용 고려 시 최적 세팅은? | [done] | [phase5_production_pg.md](phase5_production_pg.md) |
| **Phase 6** | VectorChord-BM25 + pg_tokenizer | Block-WeakAnd BM25 + 한국어 토크나이저 연결, 기성 확장 조합으로 해결 시도 | [done] | [phase6_vectorchord_bm25.md](phase6_vectorchord_bm25.md) |
| **Phase 7** | PostgreSQL 스케일링 + 하이브리드 | pg_textsearch AND/OR vs VectorChord vs pl/pgsql 스케일링 + BM25/Dense/RRF/Bayesian 하이브리드 벤치마크 | [done] | [phase7_scaling_comparison.md](phase7_scaling_comparison.md) |
| **Phase 8** | 외부 시스템 비교 | PostgreSQL 최선 스택 vs Elasticsearch vs Qdrant(1.15.x) vs Vespa — 한국어 하이브리드 검색 동등 조건 비교 | [next] | [phase8_system_comparison.md](phase8_system_comparison.md) |

## 실험 순서 및 의존성

```
Phase 0 (데이터)
    └── Phase 1 (형태소 분석기 독립 벤치)
            └── Phase 2 (tsvector 통합, Phase 1 top tokenizer 사용)
            └── Phase 3 (Native BM25, Phase 1 top tokenizer 사용)
                    └── Phase 4 (BM25 vs Neural, Phase 3 최선 세팅 사용)
                            └── Phase 5 (Production PG 최적화 — 운영 비용 측정)
                                    └── Phase 6 (VectorChord-BM25 + pg_tokenizer — 기성 확장 조합)
                                            └── [완료] → Phase 7 (스케일링 + 하이브리드 — pg_textsearch + VectorChord + pl/pgsql + DB-side RRF)
                                                    └── [next] Phase 8 (외부 시스템 비교 — ES/Qdrant 1.15.x/Vespa)
```

Phase 1은 Phase 2, 3의 인풋 — 반드시 먼저 완료.

## Phase 5 핵심 측정 항목

지속적 문서 추가(online ingestion) 시나리오 기준:

| 측정 항목 | 설명 |
|----------|------|
| 오프라인 빌드 throughput | docs/s (1k / 10k / 100k 규모별) |
| 온라인 문서 추가 비용 | 문서 1개 추가 시 latency (토크나이즈 + 인덱스 갱신) |
| IDF staleness | BM25 sparse: 추가 후 NDCG 저하 측정 |
| 쿼리 latency | p50/p95 (sequential) |
| 쿼리 throughput | QPS @ 1/4/8/16 concurrent |
| HNSW 효과 | BGE-M3 dense: 인덱스 유무 latency/recall tradeoff |

## 환경

- DB: PostgreSQL 18 + pgvector 0.8.2 (`pgvector/pgvector:pg18`, port 5432)
- ParadeDB: `paradedb/paradedb:latest` (port 5433) — Phase 3
- Elasticsearch: 8.11.0 (port 9200) — Phase 8
- Qdrant: v1.15.x (port 6333) — Phase 8 (multilingual tokenizer, 한국어 Unicode 경계 지원)
- Vespa: latest (port 8080/19071) — Phase 8 (Weaviate 대체 — 한국어 형태소 분석 지원 확인 필요)
- Weaviate 제외: 내장 한국어 형태소 분석기 없음
- Python: 3.12 via uv venv
- Hardware: macOS Apple Silicon (CPU-only inference)
- DB URL: `postgresql://postgres:postgres@localhost:5432/dev`
