# Phase 6: VectorChord-BM25 + textsearch_ko — 한국어 BM25

## 목표

VectorChord-BM25(Block-WeakAnd) + 기존 textsearch_ko(MeCab) 조합으로 한국어 BM25 검색을 구현한다.
textsearch_ko는 이미 PG 안에서 `public.korean` text search config로 MeCab 형태소 분석을 제공하고 있으므로, 새로운 토크나이저를 만들 필요 없이 **기존 자산을 VectorChord-BM25에 연결**하는 것이 핵심.

성공 시: **직접 포크(Phase 7/8) 없이, Rust FFI 없이** 기성 확장 + 기존 토크나이저 조합만으로 Lucene급 한국어 BM25 달성.

## 의존성

- Phase 5 완료 (한국어 BM25 문제 정량화 완료)
- textsearch_ko 확장 설치 완료 (`public.korean` config 사용 가능)
- Docker 환경 (tensorchord/vchord-suite 이미지 + textsearch_ko)

---

## 배경

### Phase 5에서 확인된 PG BM25 한국어 문제

| 확장 | 문제 |
|------|------|
| pg_textsearch (Timescale) | `<@>` 연산자 AND 매칭, OR 불가 |
| pg_search (ParadeDB) | korean_lindera = 일본어 사전 기반, NDCG=0.23 |
| pl/pgsql BM25 v2 | B-tree 테이블 → 메모리에 올려야 함, scale-up 의존 |

### 이미 가진 자산: textsearch_ko

```sql
-- 이미 잘 작동하는 한국어 형태소 분석
SELECT to_tsvector('public.korean', '한국어 검색 엔진 성능 비교');
-- → '검색':2 '비교':5 '성능':4 '엔진':3 '한국어':1

SELECT tsvector_to_array(to_tsvector('public.korean', '한국어 검색 엔진 성능 비교'));
-- → {검색,비교,성능,엔진,한국어}
```

Phase 1~5 전체에서 검증된 MeCab 기반 한국어 토크나이저. 새로 만들 필요 없음.

### VectorChord-BM25가 해결하는 것

| 항목 | VectorChord-BM25 |
|------|-----------------|
| 인덱스 구조 | **Block-WeakAnd** (posting list + WAND) — Lucene급 I/O |
| 토크나이저 | **pg_tokenizer.rs** — 분리 설계 |
| 스케일링 | posting list 구조로 대규모 유리 (B-tree 한계 해소) |
| PG 통합 | USING bm25 인덱스, SQL 네이티브 |

---

## 핵심 확인 사항 (Phase 6-0: 타당성 조사)

### 1. textsearch_ko → VectorChord-BM25 연결 경로

**우선순위 순으로 확인:**

**경로 A (Best): pg_tokenizer가 PG text search config를 직접 지원**
```sql
-- 이렇게 되면 최고
SELECT tokenize('한국어 검색 엔진', 'public.korean')::bm25vector;
```
확인: pg_tokenizer.rs 문서/소스에서 PG tsconfig wrapper 지원 여부

**경로 B (Good): pg_tokenizer에 "unicode" 또는 커스텀 토크나이저로 tsvector 출력을 먹일 수 있음**
```sql
-- tsvector_to_array로 토큰 추출 → pg_tokenizer에 pre-tokenized input으로 전달
SELECT tsvector_to_array(to_tsvector('public.korean', '한국어 검색 엔진'));
-- → {검색,엔진,한국어}
-- 이 토큰 배열을 VectorChord-BM25에 직접 전달하는 경로가 있는지
```

**경로 C (OK): pg_tokenizer를 거치지 않고 VectorChord-BM25에 직접 토큰 전달**
```sql
-- bm25vector를 직접 구성할 수 있는지
-- 예: tokenize() 대신 수동으로 bm25vector 생성
```

**경로 D (Fallback): pg_tokenizer.rs에 PG tsconfig wrapper 추가 (소스 수정)**
- pg_tokenizer.rs의 tokenizer trait에 `PostgresConfigTokenizer` 구현
- `to_tsvector()` + `tsvector_to_array()`를 내부적으로 호출
- 소스 수정이지만 FFI 없이 PG 내부 함수 호출만 → 난이도 낮음

### 2. Block-WeakAnd의 OR 매칭 지원

```
Q: pg_textsearch처럼 AND 매칭만 하는가, OR 매칭도 되는가?
확인 대상:
  - bm25_ops 연산자의 쿼리 파싱 로직
  - to_bm25query() 함수의 동작
  - BM25는 원래 OR 기반이므로 정상 구현이면 OR이어야 함
```

### 3. textsearch_ko와 vchord-suite Docker 호환성

```
Q: tensorchord/vchord-suite 이미지에 textsearch_ko를 추가 설치할 수 있는가?
확인: vchord-suite 기반 Dockerfile에 textsearch_ko + mecab-ko-dic 설치
대안: 기존 pgvector/pgvector:pg18 이미지에 VectorChord-BM25를 설치
```

---

## 실험 계획

### Phase 6-0: 타당성 조사 (반나절)

1. vchord-suite Docker 실행
2. pg_tokenizer 문서/소스 분석 — 경로 A~D 중 어느 것이 가능한지 확인
3. textsearch_ko 설치 호환성 확인
4. 간단한 한국어 쿼리로 tokenize() → bm25vector 변환 테스트

**Go/No-Go 판정:**
- Go (경로 A/B/C): 설정만으로 연결 → Phase 6-1로 진행
- Partial Go (경로 D): pg_tokenizer 소스 수정 필요 → 비용 대비 판단
- No-Go: 연결 불가 + 소스 수정 비용이 Phase 7/8 포크와 동급 → Phase 7로 진행

### Phase 6-1: textsearch_ko 연결 + 인덱스 구축 (1일)

- 확인된 경로(A/B/C/D)로 textsearch_ko 토큰을 VectorChord-BM25에 연결
- MIRACL + EZIS 데이터 로드
- USING bm25 인덱스 생성
- 기본 검색 동작 확인

### Phase 6-2: 벤치마크 (1일)

Phase 5와 동일 조건:
- MIRACL + EZIS NDCG@10, R@10
- latency p50/p95
- QPS@1/4/8 concurrent
- 비교 대상: pl/pgsql v2 (Phase 5), pg_textsearch AND, pg_search korean_lindera

### Phase 6-3: 스케일링 테스트 (1일)

Phase 5에서 확인한 pl/pgsql v2의 B-tree 한계를 VectorChord-BM25가 해결하는지:
- 1k / 10k / 100k docs 규모별 latency
- EXPLAIN ANALYZE — posting list vs B-tree I/O 패턴 확인
- 메모리 사용량 비교
- fastupdate 등 GIN 관련 설정과 무관함을 확인 (자체 bm25 인덱스)

---

## 핵심 가설

| 항목 | 예상 |
|------|------|
| textsearch_ko 연결 후 MIRACL NDCG | 0.60~0.65 (pl/pgsql v2 0.34 대비 향상 — OR 매칭 + MeCab) |
| latency | sub-ms ~ 수 ms (Block-WAND) |
| 스케일링 | posting list 구조로 10M+ 가능 (B-tree 대비 우위) |

성공 기준: **textsearch_ko 연결 성공 & MIRACL NDCG ≥ 0.55 & latency < 5ms**

---

## Phase 7/8과의 관계

| | Phase 6 (VectorChord-BM25) | Phase 7 (pg_textsearch 포크) | Phase 8 (pg_search 포크) |
|---|---|---|---|
| 토크나이저 | **textsearch_ko 재사용** | textsearch_ko 재사용 | MeCab FFI 신규 |
| BM25 엔진 | Block-WeakAnd (기성) | WAND (기성, AND→OR 수정) | Tantivy (기성, tokenizer 수정) |
| Rust 소스 수정 | 없음~최소 (경로 A~C) | 중간 (쿼리 파싱) | 높음 (FFI + trait) |
| upstream 호환 | **유지** | 포크로 상실 | 포크로 상실 |
| 실패 시 | Phase 7/8로 fallback | Phase 8로 fallback | pl/pgsql v2 유지 |

Phase 6의 핵심 장점: **textsearch_ko라는 이미 검증된 자산을 그대로 활용**하면서, BM25 엔진만 pl/pgsql B-tree → Block-WeakAnd posting list로 교체. Rust 소스를 건드릴 필요가 없거나 최소한만 건드림.

Phase 6이 성공하면 Phase 7/8은 **불필요**. 실패 시에만 포크 경로 진행.

---

## 출력

- `experiments/phase6_vectorchord/phase6_bench.py` — 벤치마크 스크립트
- `results/phase6/phase6_vectorchord_bm25.json` — 실험 결과
- `results/phase6/phase6_report.md` — 연결 경로 분석 + 벤치마크 리포트
