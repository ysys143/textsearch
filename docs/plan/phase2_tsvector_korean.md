# Phase 2: tsvector 한국어 통합 + PostgreSQL Native BM25

## 목표

두 가지 핵심 질문에 답한다:

1. **"PostgreSQL의 tsvector를 한국어에서 제대로 쓸 수 있는가?"**
   → PostgreSQL 네이티브 FTS 파이프라인(tsvector → GIN 인덱스 → ts_rank_cd)에 한국어 형태소 분석을 통합하는 모든 방법 비교

2. **"pg_textsearch(Timescale BM25)에 한국어 형태소 분석기를 접합할 수 있는가?"**
   → PostgreSQL text search configuration을 재사용하는 pg_textsearch가 Korean config와 동작하는지, BM25 이득이 있는지 검증

> **실제 집행 범위**: 원래 Phase 3(PostgreSQL Native BM25) 계획의 3-A(ParadeDB), 3-B(pl/pgsql BM25)까지 이 Phase에서 함께 실행됨.
> Phase 2와 3 실험을 한 번에 집행한 구조.

## 의존성

- Phase 0 완료
- Phase 1 완료 (top-3 tokenizer 확정: kiwi-cong > mecab > okt)

---

## 실험 대상 및 결과

### 2-A. textsearch_ko (MeCab 기반 C extension) — ts_rank_cd

**방법**: MeCab + mecab-ko-dic 기반 C extension. `public.korean` text search configuration.

> **주의**: Phase 2 실행 시점에 이미 enhanced 버전(OOV 통과, VV+하다, NNG 복합명사, SL)이 설치된 상태였음.
> 원본 vs enhanced 비교는 별도 A/B 실험(`phase2_textsearch_ko_ab.py`)에서 진행.

**결과**:
| 데이터셋 | NDCG@10 | R@10 | MRR | Latency p50 |
|---------|---------|------|-----|-------------|
| MIRACL  | 0.1815  | 0.1752 | 0.2432 | 0.73ms |
| EZIS    | 0.0076  | 0.0076 | 0.0076 | — |

EZIS 0.0076은 enhanced tokenizer + ts_rank_cd AND 필터 조합의 문제 (→ A/B 실험 참조).

---

### 2-B. plpython3u + kiwipiepy custom tsvector — ts_rank_cd

**방법**: PostgreSQL 프로세스 내 Python(kiwipiepy)으로 형태소 분석 → tsvector 직접 생성.

**결과**:
| 데이터셋 | NDCG@10 | R@10 | Latency p50 |
|---------|---------|------|-------------|
| MIRACL  | 0.2264  | 0.2449 | 6.21ms |
| EZIS    | 0.0924  | 0.2023 | — |

---

### 2-C. korean_bigram (C parser, Korean syllable unigram) — ts_rank_cd

**방법**: 자체 제작 C extension. 한글 음절 단위 unigram 역인덱스.

**결과**:
| 데이터셋 | NDCG@10 | R@10 | Latency p50 |
|---------|---------|------|-------------|
| MIRACL  | 0.0283  | 0.0276 | 0.63ms |
| EZIS    | 0.0000  | 0.0000 | — |

---

### 2-D. ParadeDB pg_search (Tantivy BM25) ← 원래 Phase 3-A

**방법**: ParadeDB `pg_search` extension. Rust Tantivy 기반 역인덱스, `korean_lindera` tokenizer.
별도 컨테이너(port 5433).

**결과**:
| 데이터셋 | NDCG@10 | R@10 | Latency p50 |
|---------|---------|------|-------------|
| MIRACL  | 0.2275  | 0.2605 | 2.27ms |
| EZIS    | 0.7196  | 0.9046 | — |

---

### 2-E. pg_tokenizer from scratch (Rust/pgrx) — 스킵

**방법**: pg_tokenizer 소스 분석 후 pgrx 직접 구현. 시간 상 스킵.

**결과**: NDCG@10=0.0000 (미구현)

---

### 2-F. pgroonga (Groonga FTS 엔진)

**방법**: tsvector 없이 Groonga 자체 역인덱스. 별도 컨테이너(port 5435).

**결과**:
| 데이터셋 | NDCG@10 | R@10 | Latency p50 |
|---------|---------|------|-------------|
| MIRACL  | 0.1875  | 0.2429 | 3.48ms |
| EZIS    | 0.2481  | 0.4542 | — |

---

### 2-G. pg_bigm (문자 바이그램 — 기준선)

**방법**: 형태소 분석 없음. 문자 2-gram GIN 인덱스.

**결과**:
| 데이터셋 | NDCG@10 | R@10 | Latency p50 |
|---------|---------|------|-------------|
| MIRACL  | 0.2266  | 0.2680 | 5.9ms |
| EZIS    | 0.5868  | 0.8206 | — |

---

### 2-H-a. pg_textsearch + public.korean (MeCab BM25/WAND) ← 핵심 실험

**방법**: Timescale `pg_textsearch` — Block-Max WAND 알고리즘 BM25.
`USING bm25(text) WITH (text_config='public.korean')`.

> **주의**: `text_config` 값에 schema prefix 필수 (`'public.korean'`).
> pg_textsearch 인덱스 빌더가 session search_path를 무시하고 pg_catalog만 탐색함.

**결과**:
| 데이터셋 | NDCG@10 | R@10 | MRR | Latency p50 |
|---------|---------|------|-----|-------------|
| MIRACL  | 0.3374  | 0.3844 | 0.4133 | 0.86ms |
| EZIS    | 0.8417  | 0.9008 | 0.8224 | — |

---

### 2-H-b. pg_textsearch + public.korean_bigram (C parser BM25/WAND)

**방법**: 위와 동일하나 `text_config='public.korean_bigram'` (음절 unigram config 사용).

**결과**:
| 데이터셋 | NDCG@10 | R@10 | Latency p50 |
|---------|---------|------|-------------|
| MIRACL  | 0.2642  | 0.3163 | 1.08ms |
| EZIS    | 0.8057  | 0.8931 | — |

---

### 2-I. pl/pgsql custom BM25 (from scratch) ← 원래 Phase 3-B

**방법**: plpgsql로 역인덱스 테이블 + BM25 랭킹 함수 직접 구현.

**결과**: NDCG@10=0.0000 (원래 NULL constraint 버그로 실패)

**버그 수정** (`experiments/phase2_tsvector/phase2_tsvector_comparison.py`):

두 가지 버그가 있었음:

1. **JSONB O(n²) 버그** (lines 484-493): `jsonb_set()` 루프가 토큰마다 새 JSONB 객체를 할당 → 1만 doc × 평균 62 토큰 = ~620k JSONB 객체 할당. 10k 코퍼스에서 timeout/롤백 → stats_table에 INSERT 미완료 → 이후 search 시 N=NULL.
   - **수정**: `unnest(tok_arr) t ... GROUP BY t` 단일 INSERT로 교체 (O(n))

2. **AmbiguousColumn 버그** (search function): `RETURNS TABLE(doc_id TEXT, ...)` 선언이 PL/pgSQL scope에 `doc_id` 변수를 생성 → CTE 내 bare `doc_id` 참조와 충돌.
   - **수정**: CTE 서브쿼리에 테이블 별칭 추가 (`ix.doc_id`, `dl2.doc_id` 등)

3. **GIN 인덱스 추가**: `CREATE INDEX IF NOT EXISTS {idx_table}_term_idx ON {idx_table}(term)` — 쿼리 시 term 룩업 O(n) → O(log n)

**수정 후 결과** — ts_config 파라미터 추가 후 3개 변형 비교 (10k MIRACL + EZIS):

토크나이저를 `ts_config` 파라미터로 주입하는 방식으로 리팩터링 (`unnest(to_tsvector('{ts_config}', text))`).

| 데이터셋 | variant | NDCG@10 | R@10 | MRR | p50 latency | Zero-result |
|---------|---------|---------|------|-----|-------------|-------------|
| MIRACL-ko (10k) | 2-I (pg_catalog.simple) | 0.4071 | 0.4757 | 0.4580 | 11.14ms | 1.0% |
| MIRACL-ko (10k) | 2-I-korean (public.korean) | **0.6412** | **0.8012** | **0.6191** | 10.44ms | 0.0% |
| EZIS (97 docs) | 2-I (pg_catalog.simple) | 0.8567 | 0.9733 | 0.8237 | 1.64ms | 0% |
| EZIS (97 docs) | 2-I-korean (public.korean) | **0.9290** | **0.9924** | **0.9085** | 1.72ms | 0% |

빌드: MIRACL 10k simple → 86.6s (503,852 pairs) / korean → 37.1s (456,039 pairs)

> **참고**: 이전 빌드 시간(simple 181.8s / korean 103.6s)은 2-table 설계 기준. 현재는 4-table 설계(bm25idx+bm25df+bm25doclen+bm25stats)로 재구현되어 빌드 및 검색 레이턴시 모두 개선됨.

**해석**:
- `2-I-korean` MIRACL: NDCG@10=0.6412 — pgvector-sparse BM25 (kiwi-cong) 0.6326을 능가. 순수 SQL BM25가 pgvector 의존 없이 phase3 수준 달성.
- `2-I-korean` EZIS: NDCG@10=0.9290 — 전체 2위 (Bayesian BM25+BGE-M3 dense 0.9493 다음), 순수 BM25 중 최고.
- MeCab tokenizer가 `to_tsvector('public.korean')` 을 통해 pl/pgsql에서 동작 — Python 없이 SQL만으로 형태소 분석 완전 통합.
- latency: 4-table 설계로 풀스캔 서브쿼리 제거 → MIRACL p50=10.44ms (이전 45ms 대비 4.3×), EZIS p50=1.72ms (이전 11.6ms 대비 6.7×). pg_textsearch(0.86ms)보다는 여전히 느림 — WAND 부재가 구조적 병목.

---

## 비교 매트릭스 (MIRACL 기준, 성능순)

| 방법 | 랭킹 함수 | tokenizer | NDCG@10 | Latency |
|------|---------|-----------|---------|---------|
| 2-I-korean pl/pgsql BM25+MeCab | pl/pgsql BM25 | to_tsvector(public.korean) | **0.6412** | 10.44ms |
| 2-I pl/pgsql BM25 (simple) | pl/pgsql BM25 | to_tsvector(pg_catalog.simple) | 0.4071 | 11.14ms |
| 2-H-a pg_textsearch + MeCab | BM25/WAND | C/MeCab | 0.3374 | 0.86ms |
| 2-H-b pg_textsearch + bigram | BM25/WAND | C unigram | 0.2642 | 1.08ms |
| 2-D ParadeDB pg_search | BM25/Tantivy | Rust/lindera | 0.2275 | 2.27ms |
| 2-G pg_bigm | bigm_similarity | n-gram | 0.2266 | 5.9ms |
| 2-B plpython3u+kiwi | ts_rank_cd | Python/kiwi | 0.2264 | 6.21ms |
| 2-F pgroonga | Groonga | Groonga 내장 | 0.1875 | 3.48ms |
| 2-A textsearch_ko | ts_rank_cd | C/MeCab | 0.1815 | 0.73ms |
| 2-C korean_bigram | ts_rank_cd | C unigram | 0.0283 | 0.63ms |

---

## 핵심 발견

### 1. pg_textsearch BM25가 ts_rank_cd보다 명확히 우수
같은 MeCab tokenizer 기준: ts_rank_cd 0.1815 → BM25 0.3374 (+86%).
BM25의 OR-기반 가중 스코어링이 ts_rank_cd AND 필터보다 Korean 자연어 쿼리에 적합.

### 2. textsearch_ko + BM25가 단연 최선의 PostgreSQL-native 조합
pg_textsearch(BM25) × enhanced MeCab tokenizer = MIRACL 0.3374, EZIS 0.8417.
A/B 실험에서 BM25 조합으로 EZIS 0.0076 → 0.9238 회복 확인.

### 3. Tokenizer × 랭킹 함수 상호작용이 결과를 지배
Enhanced tokenizer는 BM25에서 극적 향상, ts_rank_cd에서 오히려 역효과 (→ `phase2_textsearch_ko_ab.py` 참조).

### 4. ParadeDB(Tantivy)의 의외의 부진
BM25 엔진임에도 MIRACL 0.2275로 pg_textsearch(0.3374)에 크게 뒤짐.
원인: `korean_lindera` tokenizer 품질이 MeCab(mecab-ko-dic)보다 낮음.

---

## Docker 구성

| 서비스 | 이미지 | 포트 | profile |
|--------|--------|------|---------|
| PostgreSQL 18 (기본) | `pgvector/pgvector:pg18` | 5432 | `core` |
| pgroonga | `groonga/pgroonga:latest` | 5435 | `phase2-pgroonga` |
| ParadeDB | `paradedb/paradedb:latest` | 5433 | `phase2-paradedb` |
| textsearch-pg-baseline | `pgvector/pgvector:pg18` | 5434 | A/B 실험용 |

---

## 출력

- `results/phase2/phase2_tsvector_comparison.json` — 전체 결과
- `results/phase2/phase2_textsearch_ko_ab.json` — 2-A vs 2-C A/B 실험 (2×2 매트릭스)
- `experiments/phase2_tsvector/phase2_tsvector_comparison.py`
- `experiments/phase2_tsvector/phase2_textsearch_ko_ab.py`
