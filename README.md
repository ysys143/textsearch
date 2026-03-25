# textsearch — 한국어 텍스트 검색 벤치마크

PostgreSQL만으로 Elasticsearch를 대체할 수 있을까? 형태소 분석기부터 하이브리드 검색까지, 8단계 실험으로 직접 측정했다.

## TL;DR

MeCab 형태소 분석 + pg_textsearch BM25 + pgvector HNSW + DB-side RRF 조합이 ES/Qdrant/Vespa 대비 품질은 동등하고, latency는 2~5배 빠르다.

```
textsearch_ko (MeCab) + pg_textsearch BM25 + pgvector HNSW + DB-side RRF
→ MIRACL NDCG 0.77 @1.79ms / EZIS NDCG 0.86 @0.92ms
```

## 왜 이 실험을 했나

한국어 검색은 영어와 다르다. 영어에서 "running"을 "run"으로 줄이는 스테밍은 간단하지만, 한국어에서 "먹었다"를 "먹-"으로 분리하려면 형태소 분석기가 필요하다. 이게 없으면 "먹는", "먹고", "먹었던" 같은 활용형을 같은 문서로 연결할 수 없고, BM25 검색 품질이 반토막 난다.

Elasticsearch는 nori라는 한국어 형태소 분석기를 내장하고 있어서 이 문제를 바로 해결해준다. 반면 PostgreSQL의 기본 full-text search는 한국어를 지원하지 않는다. textsearch_ko라는 확장을 설치하면 MeCab 형태소 분석을 tsvector에 연결할 수 있고, pg_textsearch 확장을 쓰면 BM25 스코어링까지 가능해진다.

여기에 pgvector로 밀집 벡터 검색을 더하고, SQL CTE 함수로 BM25 + Dense 결과를 RRF(Reciprocal Rank Fusion)로 합치면, PostgreSQL 하나로 하이브리드 검색 파이프라인이 완성된다. 별도 검색 엔진 없이 단일 DB만으로 운영할 수 있다는 뜻이다.

pg_textsearch 같은 확장이 PostgreSQL에 BM25를 구현해주긴 한다. 하지만 BM25 확장이 존재하는 것과 한국어가 잘 되는 것은 별개 문제다. 핵심은 MeCab 같은 외부 형태소 분석기를 PostgreSQL 토크나이저로 실제로 잘 붙일 수 있는가였다. textsearch_ko가 MeCab을 tsvector 파이프라인에 연결해주고, pg_textsearch가 그 tsvector 위에 BM25 인덱스를 만들어주는 이 조합이 실제로 동작하는지, 품질이 충분한지를 확인하는 것이 이 실험의 핵심이었다.

8단계에 걸쳐 측정했다. Phase 1에서 형태소 분석기를 고르고, Phase 2에서 PostgreSQL tsvector에 연결하고, Phase 3에서 BM25 구현 방법을 비교하고, Phase 7에서 하이브리드 검색을 완성한 뒤, Phase 8에서 Elasticsearch, Qdrant, Vespa와 동등 조건으로 붙여봤다.

## 실험에서 알게 된 것

### 형태소 분석기가 한국어 BM25의 성패를 가른다

Phase 1에서 MeCab, Kiwi, Okt 세 가지 형태소 분석기를 비교했다. MeCab이 속도와 품질 모두에서 1위였고, 이후 모든 실험의 기본 토크나이저가 됐다.

Phase 8에서 형태소 분석기의 중요성이 더 분명해졌다. MeCab이나 nori처럼 형태소 분석을 하는 시스템(PostgreSQL, Elasticsearch)은 MIRACL BM25에서 NDCG 0.61~0.64를 달성했다. 반면 ICU 유니코드 경계 분리만 하는 Vespa는 0.41, Qdrant의 charabia 토크나이저는 사실상 0에 가까운 점수를 받았다. 토크나이저 하나가 품질 차이의 대부분을 설명한다.

재미있는 건 Elasticsearch의 nori 토크나이저도 AND matching에서는 취약하다는 점이다. nori의 `decompound_mode: mixed`가 복합어를 과도하게 분해해서, 모든 토큰이 존재해야 하는 AND 조건에서는 NDCG가 0.13까지 떨어진다. 같은 AND 조건에서 PostgreSQL의 textsearch_ko는 0.64를 유지했다. ES의 높은 BM25 점수(0.61)는 OR matching 기본값 덕분이었다.

### 도메인에 따라 최적 방법이 뒤집힌다

이 실험의 가장 중요한 설계는 성격이 다른 두 데이터셋을 병행 평가한 것이다.

MIRACL은 한국어 위키피디아에서 추출한 일반 지식 데이터셋이다. 여기서는 Dense 벡터 검색(NDCG 0.79)이 BM25(0.64)를 크게 앞선다. 의미론적 유사도가 정확한 키워드 매칭보다 중요한 도메인이다.

EZIS는 Oracle 데이터베이스 모니터링 매뉴얼에서 만든 기술 문서 QA 셋이다. 여기서는 BM25(0.92)가 Dense(0.80)를 압도한다. "ORA-01555"나 "DBMS_STATS" 같은 정확한 용어 매칭이 의미론적 유사도보다 중요하기 때문이다.

같은 PostgreSQL 스택이, 같은 하이브리드 설정이, 데이터 성격에 따라 반대 결과를 내놓는다. "어떤 방법이 항상 최고"라는 결론은 불가능하다는 뜻이고, 하이브리드(RRF)가 도메인을 모를 때의 안전한 선택인 이유다.

### PostgreSQL이 외부 시스템보다 느리지 않다

직관적으로 전용 검색 엔진이 범용 DB보다 빠를 것 같지만, 측정 결과는 반대였다. PostgreSQL의 DB-side RRF는 BM25 쿼리와 Dense 쿼리를 SQL CTE 안에서 실행하고 결과를 합친다. 애플리케이션과 DB 사이의 왕복이 한 번이다. 반면 ES나 Qdrant는 HTTP/JSON을 통해 요청을 보내고 받는데, 이 네트워크 오버헤드가 쿼리 자체보다 클 수 있다.

MIRACL 10K 기준으로 PostgreSQL RRF는 p50 1.79ms, ES retriever.rrf는 5.18ms, Qdrant prefetch RRF는 4.54ms, Vespa hybrid는 4.14ms였다. 2~3배 차이다. 물론 이건 단일 노드 로컬 환경에서의 warm-cache 측정이고, 분산 환경이나 대규모 데이터에서는 결과가 달라질 수 있다.

한 가지 주의할 점은 Dense 검색의 latency에 BGE-M3 임베딩 추론 시간(~200ms)이 빠져 있다는 것이다. 쿼리 임베딩을 사전 계산해두고 벤치마크했기 때문에 retrieval-only 수치다. 실제 서비스에서는 임베딩 추론 비용을 따로 고려해야 한다.

### Qdrant에는 self-hosted BM25가 없다

Qdrant는 벡터 검색 전용 엔진으로서 구조적으로 가장 우수하다. 양자화, 필터링, 멀티테넌시, 대규모 분산 등 벡터 검색에 필요한 기능이 가장 풍부하고, 의미론적 검색(semantic search) 시나리오에서 가장 성숙한 도구다. 하지만 한국어 텍스트 검색은 구조적으로 약하다.

`qdrant/bm25`라는 서버사이드 BM25 모델이 있지만 Qdrant Cloud 전용이다. self-hosted에서는 쓸 수 없다. 텍스트 페이로드 인덱스(`TextIndexParams`)는 있지만 이건 boolean 필터라서 스코어가 안 나온다. 외부에서 MeCab으로 토크나이징한 뒤 sparse vector로 넣어봤는데, 이건 TF x IDF일 뿐 진짜 BM25가 아니다. 문서 길이 정규화(k1, b 파라미터)가 빠져 있어서 NDCG가 0.36에 그쳤다.

FastEmbed의 BM42 모델도 시도했다. 트랜스포머 어텐션 기반으로 토큰 중요도를 매기는 방식인데, 한국어 EZIS에서 NDCG 0.48이었다. 형태소 BM25(0.92)의 절반 수준이다. Qdrant로 한국어 하이브리드 검색을 하려면 Dense-only로 가거나, BM25는 다른 시스템에 맡겨야 한다.

### Vespa는 한국어 형태소 분석을 기본 지원하지 않는다

Vespa 자체는 BM25 + ANN hybrid를 잘 지원하는 시스템이다. rank-profile에서 `bm25(text) + closeness(field, dense_vec)` 같은 선형 결합을 선언적으로 정의할 수 있다. 하지만 기본 토크나이저가 ICU(유니코드 경계 분리)라서 한국어 형태소 분석을 안 한다.

`vespa-linguistics-ko`라는 MeCab 기반 한국어 패키지가 있지만, 커스텀 Vespa 빌드가 필요하고 표준 Docker 이미지에는 포함되어 있지 않다. 이번 실험에서는 기본 ICU로 진행했는데, MIRACL BM25가 NDCG 0.41로 형태소 시스템(0.61~0.64)보다 상당히 낮았다. EZIS 기술 문서에서는 0.81로 그나마 선방했는데, 영문 약어(DBMS, ORA 등)가 많아서 형태소 분석 없이도 매칭이 된 것으로 보인다.

Hybrid에서는 더 심각한 문제가 드러났다. ICU BM25가 노이즈를 생성해서 `0.1*bm25 + closeness` 선형 결합 결과가 Dense-only(0.79)보다 나쁜 NDCG 0.45를 기록했다. BM25 레그가 약하면 하이브리드가 오히려 품질을 깎는다.

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
