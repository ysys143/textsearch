# textsearch — 한국어 텍스트 검색 벤치마크

PostgreSQL만으로 Elasticsearch를 대체할 수 있을까? 형태소 분석기부터 하이브리드 검색까지, 8단계 실험으로 직접 측정했다.

## TL;DR

MeCab 형태소 분석 + pg_textsearch BM25 + pgvector HNSW + DB-side RRF 조합이 ES/Qdrant/Vespa 대비 품질은 동등하고, latency는 2~5배 빠르다.

```
textsearch_ko (MeCab) + pg_textsearch BM25 + pgvector HNSW + DB-side RRF
→ MIRACL NDCG 0.77 @1.79ms / EZIS NDCG 0.86 @0.92ms
```

## 왜 이 실험을 했나

한국어 검색은 영어와 다르다. "먹었다"를 "먹"으로 분리해야 "먹는", "먹고"와 같은 문서를 찾을 수 있다. 이 형태소 분석이 안 되면 BM25 품질이 반토막 난다 (NDCG 0.64 → 0.36).

PostgreSQL 안에서 형태소 분석 + BM25 + 벡터 검색 + 하이브리드 융합을 전부 처리할 수 있다면, 별도 검색 엔진 없이 단일 DB로 운영할 수 있다. 이게 실제로 가능한지, 품질과 속도가 충분한지를 검증하는 것이 목표였다.

## 실험에서 알게 된 것

**형태소 분석기가 한국어 BM25의 성패를 가른다.**
MeCab/nori(형태소)를 쓰면 NDCG 0.61~0.64, ICU/charabia(비형태소)를 쓰면 0.36~0.41. 토크나이저 하나가 품질 차이의 대부분을 설명한다.

**같은 데이터에서 BM25와 Dense가 역전된다.**
일반 위키(MIRACL)에서는 Dense(0.79)가 BM25(0.64)보다 낫고, 기술 매뉴얼(EZIS)에서는 BM25(0.92)가 Dense(0.80)보다 낫다. 도메인을 모르면 하이브리드가 안전한 선택이다.

**PostgreSQL이 외부 시스템보다 느리지 않다.**
DB-side RRF가 1.79ms인데, ES는 5.18ms, Qdrant는 4.54ms, Vespa는 4.14ms. 네트워크 왕복이 없는 DB-side 실행이 빠를 수밖에 없다.

**Qdrant에는 self-hosted BM25가 없다.**
`qdrant/bm25` 서버모델은 Cloud 전용이고, 텍스트 인덱스(charabia)는 필터 전용이라 스코어가 안 나온다. FastEmbed BM42도 NDCG 0.48로 형태소 BM25의 절반 수준.

## 주요 수치

### PostgreSQL 내부 비교 (Phase 1~7)

| 방법 | MIRACL NDCG@10 | EZIS NDCG@10 | p50 |
|------|:---:|:---:|:---:|
| pg_textsearch BM25 (MeCab) | 0.6385 | 0.9162 | 0.44ms |
| Dense (BGE-M3 HNSW) | 0.7904 | 0.8041 | 1.2ms |
| RRF hybrid (DB-side) | 0.7683 | 0.8641 | 1.79ms |
| Bayesian hybrid (DB-side) | 0.7272 | 0.9249 | 9.55ms |

### 외부 시스템 비교 (Phase 8)

| 시스템 | MIRACL Hybrid | EZIS Hybrid | p50 |
|--------|:---:|:---:|:---:|
| PostgreSQL (RRF) | 0.7683 | 0.8641 | 1.79ms |
| ES 8.17 (nori, retriever.rrf) | 0.7501 | 0.8769 | 5.18ms |
| Qdrant 1.15 (MeCab sparse + dense) | 0.6924 | 0.8394 | 4.54ms |
| Vespa (ICU + HNSW) | 0.4463 | 0.8125 | 4.14ms |

상세 분석: [docs/results/phase8_system_comparison.md](docs/results/phase8_system_comparison.md)

## 데이터셋

| 데이터셋 | 성격 | 쿼리 | 코퍼스 | 특징 |
|---------|------|:---:|:---:|------|
| MIRACL-ko | 일반 위키피디아 | 213 | 10K | Dense 유리 |
| EZIS Oracle Manual | 기술 매뉴얼 | ~120 | ~200 | BM25 유리 |

성격이 다른 두 데이터셋을 병행 평가해서, "어떤 방법이 항상 최고"라는 착각을 방지했다.

## 빠른 시작

```bash
# 복제 및 환경 구성
git clone https://github.com/ysys143/textsearch.git
cd textsearch
uv venv && source .venv/bin/activate && uv sync

# PostgreSQL + pgvector 시작
docker compose --profile core up -d

# 접속 확인 (host=localhost, port=5432, user=postgres, pw=postgres, db=dev)
psql -h localhost -U postgres -d dev -c "SELECT version();"
```

## 실험 단계

| Phase | 무엇을 했나 | 상태 |
|:---:|------|:---:|
| 0 | MIRACL-ko + EZIS 데이터 준비 | done |
| 1 | 형태소 분석기 비교 (MeCab vs Kiwi vs Okt) | done |
| 2 | PostgreSQL tsvector 한국어 통합 | done |
| 3 | PostgreSQL 내부 BM25 구현 비교 | done |
| 4 | BM25 vs Neural (Dense, SPLADE) | done |
| 5 | Production 최적화 (incremental, concurrency) | done |
| 6 | VectorChord-BM25 스케일링 (1K/10K/100K) | done |
| 7 | pg_textsearch 스케일링 + 하이브리드 (RRF, Bayesian) | done |
| 8 | 외부 시스템 비교 (ES 8.17 / Qdrant 1.15 / Vespa) | done |

```
Phase 0 → Phase 1 → Phase 2 (tsvector)
                   → Phase 3 (BM25) → Phase 4 (vs Neural) → Phase 5 (Production)
                     → Phase 6 (VectorChord) → Phase 7 (하이브리드) → Phase 8 (외부 비교)
```

### Phase 8 실행 예시

```bash
# 임베딩 내보내기 (PG → JSON)
uv run python3 experiments/phase8_system_comparison/export_embeddings.py

# ES 벤치마크
docker compose --profile phase8-es up -d
uv run python3 experiments/phase8_system_comparison/phase8_es.py

# Qdrant 벤치마크
docker compose --profile phase8-qdrant up -d
uv run python3 experiments/phase8_system_comparison/phase8_qdrant.py

# Vespa 벤치마크
docker compose --profile phase8-vespa up -d
uv run python3 experiments/phase8_system_comparison/phase8_vespa.py --vespa-url http://localhost:8090

# 통합 리포트
uv run python3 experiments/phase8_system_comparison/phase8_report.py
```

## 프로젝트 구조

```
textsearch/
├── data/                           # MIRACL-ko + EZIS 데이터
├── docs/
│   ├── plan/                       # 실험 계획서 (Phase 0~8)
│   ├── results/                    # 실험 결과 분석
│   │   ├── phase7_pg_best_stack.md
│   │   ├── phase8_system_comparison.md
│   │   ├── phase8_compare_es.md
│   │   ├── phase8_compare_qdrant.md
│   │   └── phase8_compare_vespa.md
│   └── source-analysis/            # PG 확장 소스 분석
├── experiments/                    # 실험 코드 (Phase별)
├── extensions/                     # 커스텀 PG 확장
│   ├── textsearch_ko/              # MeCab 한국어 토크나이저
│   └── korean_bigram/              # 한국어 음절 파서
├── vendor/                         # 외부 참조 소스
├── results/                        # 실험 결과 JSON
├── docker-compose.yml              # PG, ES, Qdrant, Vespa
└── README.md
```

## 관련 프로젝트

이 벤치마크에서 사용한 PostgreSQL 확장과 외부 시스템:

| 프로젝트 | 역할 |
|---------|------|
| [mecab-ko](https://github.com/hephaex/mecab-ko) | 한국어 형태소 분석기 (MeCab fork) |
| [textsearch_ko](https://github.com/i0seph/textsearch_ko) | PostgreSQL 한국어 FTS 확장 |
| [textsearch_ko fork](https://github.com/ysys143/textsearch_ko) | 이 프로젝트에서 사용하는 fork |
| [pg_textsearch](https://github.com/timescale/pg_textsearch) | Timescale BM25 확장 |
| [pgvector](https://github.com/pgvector/pgvector) | PostgreSQL 벡터 검색 (HNSW/IVFFlat) |
| [pgvectorscale](https://github.com/timescale/pgvectorscale) | Timescale DiskANN 벡터 인덱스 |
| [VectorChord](https://github.com/tensorchord/VectorChord) | DiskANN + Block-WeakAnd BM25 |
| [BGE-M3](https://huggingface.co/BAAI/bge-m3) | 다국어 1024-dim 임베딩 모델 |

## 시스템 요구사항

- Python 3.12+
- PostgreSQL 15+ (pgvector 0.8.2)
- Docker 24.0+ (메모리 8GB 이상 권장)
- macOS 또는 Linux

## 라이센스

MIT

---

**Last Updated**: 2026-03-25 | Phase 8 완료 | PostgreSQL 스택 유지 권장
