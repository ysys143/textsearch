# 한국어 텍스트 검색 벤치마크 (textsearch)

PostgreSQL이 Elasticsearch를 대체할 수 있는가? — 한국어 텍스트 검색 시스템 비교 연구

## 연구 목표

**핵심 질문**: 한국어 텍스트 검색에서 PostgreSQL이 Elasticsearch를 대체할 수 있는가?

각 계층별(형태소 분석기, FTS 통합, BM25 구현, 검색 엔진)로 최적 방법론을 실험 기반으로 검증합니다.

### 핵심 통찰

- **데이터 성격에 따른 역전**: 일반 위키피디아(neural 유리) vs 기술 매뉴얼(BM25 유리) — 동일한 방법이 모든 도메인에서 최적이 아님
- **Hybrid 검색이 최강**: BM25 + 밀집 벡터(BGE-M3) 융합이 NDCG@10 0.95 달성
- **PostgreSQL 확장 가능성**: pl/pgsql 기반 BM25로 ~100M 문서까지 확장 가능, 이후 ES 전환 검토

## 주요 결과

### Phase 1~5 완료 — 핵심 수치

#### BM25-only 티어

| 방법 | MIRACL NDCG@10 | EZIS NDCG@10 | p50 Latency |
|------|:---:|:---:|:---:|
| **pl/pgsql BM25 + MeCab** | 0.6412 | 0.9290 | 10ms |
| pgvector-sparse (kiwi) | 0.6326 | 0.9455 | 4ms |
| pg_textsearch + MeCab | 0.3374 | 0.8488 | 0.86ms |
| pg_search (korean_lindera) | 0.2348 | — | 2ms |

#### Hybrid 검색 (BM25 + 밀집 벡터)

| 방법 | MIRACL NDCG@10 | EZIS NDCG@10 | p50 Latency |
|------|:---:|:---:|:---:|
| **BGE-M3 dense** | 0.7915 | 0.8060 | 253ms |
| Bayesian BM25 + BGE-M3 | 0.7476 | **0.9493** | 379ms |
| splade-ko sparse | 0.6962 | 0.8998 | 105ms |

#### Production 권장 구성

- **BM25 컴포넌트**: pl/pgsql v2 (stats 테이블 분리, incremental update 지원)
- **Hybrid 최적**: Bayesian BM25 + BGE-M3 밀집 벡터 융합
- **확장성**: PostgreSQL scale-up으로 ~100M 문서까지 가능

## 데이터셋

| 데이터셋 | 성격 | 쿼리 수 | 코퍼스 크기 | 특징 |
|---------|------|:---:|:---:|------|
| **MIRACL-ko** | 일반 위키피디아 | 213 | 10k passages | Neural 방법 유리 |
| **EZIS Oracle Manual** | 도메인 기술 매뉴얼 | ~120 | ~200 chunks | BM25 방법 유리 |

두 데이터셋을 병행 평가하여 도메인에 따른 방법론 역전 현상을 핵심 인사이트로 도출.

## 프로젝트 구조

```
textsearch/
├── data/                           # MIRACL-ko + EZIS 데이터
│   ├── miracl_ko_passages/
│   └── ezis_qa/
│
├── docs/
│   ├── plan/                       # Phase 0~9 실험 계획
│   │   ├── README.md               # 전체 실험 로드맵
│   │   ├── phase0_data_prep.md
│   │   ├── phase1_morphological_analyzers.md
│   │   ├── phase2_tsvector_korean.md
│   │   ├── phase3_native_bm25.md
│   │   ├── phase4_bm25_vs_neural.md
│   │   ├── phase5_production_pg.md
│   │   ├── phase6_vectorchord_bm25.md  [next]
│   │   ├── phase7_textsearch_fork.md   [planned]
│   │   ├── phase8_pgsearch_fork.md     [planned]
│   │   └── phase9_system_comparison.md [planned]
│   │
│   ├── results/                    # 실험 결과 분석
│   │   ├── README.md               # 결과 요약
│   │   ├── phase1_morphological_analyzers.md
│   │   ├── phase2_tsvector_korean.md
│   │   ├── phase3_native_bm25.md
│   │   ├── phase4_bm25_vs_neural.md
│   │   └── phase5_production_pg.md
│   │
│   └── source-analysis/            # PG 확장 소스 분석
│       ├── textsearch_ko_analysis.md
│       └── pg_search_analysis.md
│
├── experiments/                    # 실험 코드
│   ├── phase1_morphological/
│   │   └── phase1_analyzer_comparison.py
│   ├── phase2_tsvector/
│   │   ├── phase2_korean_config.py
│   │   └── phase2_tokenizer_integration.py
│   ├── phase3_native_bm25/
│   │   ├── phase3_pl_pgsql_bm25.py
│   │   ├── phase3_pgvector_sparse.py
│   │   └── phase3_bm25_benchmark.py
│   ├── phase4_bm25_vs_neural/
│   │   ├── phase4_neural_sparse.py
│   │   ├── phase4_bm25_comparison.py
│   │   └── phase4_hybrid_fusion.py
│   └── phase5_production/
│       ├── phase5_pl_pgsql_v2.py
│       ├── phase5_incremental_ingestion.py
│       └── phase5_production_benchmark.py
│
├── extensions/                     # PostgreSQL 확장 (포크/커스텀)
│   ├── textsearch_ko/              # MeCab 기반 한국어 토크나이저
│   │   ├── README.md
│   │   ├── src/
│   │   └── sql/
│   └── korean_bigram/              # 한국어 음절 파서
│       ├── README.md
│       ├── src/
│       └── sql/
│
├── vendor/                         # 참조 소스 (원본)
│   ├── textsearch_ko_original/
│   ├── pg_textsearch_original/
│   ├── pg_search_original/
│   └── pg_bigm_original/
│
├── src/                            # 공유 Python 모듈
│   ├── bm25_module.py              # BM25 임베더 + pl/pgsql 함수
│   ├── tokenizer_utils.py          # 형태소 분석기 래퍼
│   ├── data_loader.py              # MIRACL/EZIS 로더
│   └── metrics.py                  # NDCG, Recall, MRR 계산
│
├── results/                        # 실험 결과 (JSON + Markdown)
│   ├── phase1_results.json
│   ├── phase2_results.json
│   ├── phase3_results.json
│   ├── phase4_results.json
│   └── phase5_results.json
│
├── docker-compose.yml              # 인프라 (PG, ES, Qdrant, Weaviate)
├── pyproject.toml                  # 프로젝트 메타 (uv)
├── requirements.txt                # 의존성
└── README.md                       # 이 파일
```

## 빠른 시작

### 1. 환경 구성

```bash
# 저장소 복제
git clone https://github.com/yourusername/textsearch.git
cd textsearch

# Python venv 생성 (Python 3.12+)
python3 -m venv .venv
source .venv/bin/activate  # macOS/Linux
# 또는
.venv\Scripts\activate     # Windows

# 의존성 설치
pip install -r requirements.txt
```

또는 **uv** 사용 (더 빠름):

```bash
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

### 2. 데이터베이스 시작

```bash
# PostgreSQL + pgvector 시작 (core 프로필)
docker compose --profile core up -d

# 상태 확인
docker compose ps

# 연결 확인
psql -h localhost -U postgres -d dev -c "SELECT version();"
```

접속 정보:
- **Host**: localhost
- **Port**: 5432
- **User**: postgres
- **Password**: postgres
- **Database**: dev

### 3. 데이터 준비 (Phase 0)

```bash
# MIRACL-ko + EZIS 데이터 다운로드 및 전처리
python3 experiments/phase1_morphological/phase1_analyzer_comparison.py --prepare-data
```

### 4. 실험 실행

#### Phase 1: 형태소 분석기 비교

```bash
# 한국어 형태소 분석기 벤치마크 (MeCab vs Kiwi vs Okt)
python3 experiments/phase1_morphological/phase1_analyzer_comparison.py

# 결과: results/phase1_results.json
```

#### Phase 2: PostgreSQL 한국어 FTS 통합

```bash
# textsearch_ko (MeCab) + tsvector 통합
python3 experiments/phase2_tsvector/phase2_korean_config.py

# 결과: results/phase2_results.json
```

#### Phase 3: PostgreSQL Native BM25

```bash
# pl/pgsql BM25 vs pgvector-sparse vs pg_textsearch
python3 experiments/phase3_native_bm25/phase3_bm25_benchmark.py

# 결과: results/phase3_results.json
```

#### Phase 4: BM25 vs Neural 검색

```bash
# BM25 + BGE-M3 dense + splade-ko sparse 비교
python3 experiments/phase4_bm25_vs_neural/phase4_hybrid_fusion.py

# 결과: results/phase4_results.json
```

#### Phase 5: Production 최적화

```bash
# pl/pgsql v2 (stats 분리) incremental 벤치마크
python3 experiments/phase5_production/phase5_production_benchmark.py

# 결과: results/phase5_results.json
```

## 실험 의존성 맵

```
Phase 0 (데이터 준비)
    ↓
Phase 1 (형태소 분석기 벤치)
    ├─→ Phase 2 (tsvector 통합, Phase 1 최선 토크나이저 사용)
    ├─→ Phase 3 (Native BM25, Phase 1 최선 토크나이저 사용)
    │       ↓
    │   Phase 4 (BM25 vs Neural, Phase 3 최선 구성 사용)
    │       ↓
    │   Phase 5 (Production 최적화)
    │       ↓
    │   Phase 6 (VectorChord-BM25 + pg_tokenizer) [NEXT]
    │       ├─→ [성공] → Phase 9 (시스템 비교)
    │       └─→ [실패] → Phase 7 (pg_textsearch 포크)
    │               ↓
    │           Phase 8 (pg_search 포크)
    │               ↓
    │           Phase 9 (시스템 비교)
```

## 현재 상태

| Phase | 주제 | 상태 | 진행률 |
|-------|------|:---:|:---:|
| **Phase 0** | 데이터 준비 | done | 100% |
| **Phase 1** | 형태소 분석기 비교 | done | 100% |
| **Phase 2** | tsvector 한국어 통합 | done | 100% |
| **Phase 3** | PostgreSQL Native BM25 | done | 100% |
| **Phase 4** | BM25 vs Neural 검색 | done | 100% |
| **Phase 5** | Production 최적화 | done | 100% |
| **Phase 6** | VectorChord-BM25 + pg_tokenizer | next | 0% |
| **Phase 7** | pg_textsearch 포크 (fallback) | planned | 0% |
| **Phase 8** | pg_search 포크 (fallback) | planned | 0% |
| **Phase 9** | 최종 시스템 비교 (PG vs ES vs Qdrant vs Weaviate) | planned | 0% |

## Phase 5 결론 — Production 권장 구성

### BM25 컴포넌트: pl/pgsql v2

**선택 이유**:
- R1. Incremental 업데이트 지원 (trigger 기반)
- R2. 애플리케이션 토크나이저 불필요 (DB-side MeCab)
- R3. DB-managed 인덱스 (자동 관리)
- R4. Document-index 일관성 (query-time IDF)
- R5 제외 (직접 구현이지만 유지보수 가능)

**성능**:
- MIRACL NDCG@10: **0.3355**
- EZIS NDCG@10: **0.8926**
- p50 Latency: **3.15ms**
- QPS@8 concurrent: **252.5 ops/s**

### Hybrid 검색: Bayesian BM25 + BGE-M3 Dense

**최우수 조합**:
- MIRACL NDCG@10: 0.7476
- EZIS NDCG@10: **0.9493** (최고)
- p50 Latency: 379ms

## 확장 및 기여

### PostgreSQL 확장

```bash
# textsearch_ko (MeCab 한국어 토크나이저) 컴파일
cd extensions/textsearch_ko
make clean && make && make install

# Korean 설정 생성
psql -d dev -c "CREATE TEXT SEARCH CONFIGURATION public.korean \
  (PARSER = default);"

# 테스트
psql -d dev -c "SELECT to_tsvector('korean', '한국어 텍스트 검색');"
```

### 실험 추가

새로운 Phase를 추가하려면:

1. `docs/plan/phase{N}_*.md` 생성 (실험 계획)
2. `experiments/phase{N}_*/` 디렉토리 생성
3. 벤치마크 스크립트 작성
4. 결과를 `docs/results/phase{N}_*.md`에 문서화

## 주요 의존성

| 라이브러리 | 용도 |
|-----------|------|
| `kiwipiepy` | 한국어 형태소 분석 (Kiwi) |
| `python-mecab-ko` | MeCab 한국어 바인딩 |
| `sentence-transformers` | BGE-M3 임베딩 (Hybrid 검색) |
| `transformers` | SPLADE-ko 모델 |
| `psycopg2-binary` | PostgreSQL 클라이언트 |
| `pgvector` | pgvector 확장 클라이언트 |
| `elasticsearch` | Elasticsearch 클라이언트 (Phase 9) |
| `qdrant-client` | Qdrant 클라이언트 (Phase 9) |

## 시스템 요구사항

- **Python**: 3.12+
- **PostgreSQL**: 15+ (pgvector 0.8.2 포함)
- **Docker**: 24.0+
- **메모리**: 최소 8GB (4GB PG + 4GB 예비)
- **디스크**: 50GB+ (인덱스 + 모델)
- **OS**: macOS, Linux (WSL2 on Windows)

## 라이센스

MIT

## 참고 자료

- **MIRACL**: https://huggingface.co/datasets/miracl/miracl
- **PostgreSQL FTS**: https://www.postgresql.org/docs/current/textsearch.html
- **pgvector**: https://github.com/pgvector/pgvector
- **BGE-M3**: https://huggingface.co/BAAI/bge-m3
- **SPLADE-ko**: https://huggingface.co/sslab-public/splade-ko-doc

## 기여 및 문의

이슈, PR, 제안 환영합니다.

---

**Last Updated**: 2026-03-24 | **Phase 5 완료** | **Phase 6 시작 예정**
