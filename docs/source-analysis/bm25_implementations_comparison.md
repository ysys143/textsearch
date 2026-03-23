# PostgreSQL BM25 구현 3종 비교 분석

> 대상: pl/pgsql custom BM25 vs pg_textsearch (Timescale) vs ParadeDB pg_search (Tantivy)
> 실험: Phase 2, Method 2-I / 2-I-korean / 2-H-a / 2-D
> 소스: `experiments/phase2_tsvector/phase2_tsvector_comparison.py`

---

## 1. 아키텍처 전체 비교

| 차원 | pl/pgsql custom BM25 | pg_textsearch (Timescale) | ParadeDB pg_search |
|------|---------------------|--------------------------|-------------------|
| **구현 언어** | PL/pgSQL (SQL proc) | C (PG-native extension) | Rust (pgrx 바인딩) |
| **인덱스 구조** | 일반 PG 테이블 (`bm25idx_*`) | tsvector posting list (GIN) | Tantivy segment (PG 외부 파일) |
| **인덱스 위치** | PG tablespace 내 | PG tablespace 내 | OS filesystem (별도) |
| **알고리즘** | BM25 (WAND 없음, 풀스캔) | Block-Max WAND BM25 | Tantivy BM25 (Lucene 유사) |
| **조기 종료** | [X] 없음 | [O] WAND early termination | [WARN] segment-level 병렬 |
| **토크나이저** | 플러그형 (`ts_config` 파라미터로 임의 tsvector config 지정) | 플러그형 (PG text search config) | 내장 lindera (교체 불가) |
| **한국어 품질** | [O] `public.korean` 지정 시 최고 / [X] `pg_catalog.simple` 시 최하 | [O] 최고 (MeCab mecab-ko-dic) | [WARN] 중간 (lindera, 어미 미처리) |
| **쿼리 연산자** | `SELECT ... FROM fn_search(query, k)` | `ORDER BY text <@> query` | `WHERE t @@@ query_str` + `paradedb.score(id)` |
| **점수 노출** | [O] RETURNS TABLE(score FLOAT) | [X] ORDER BY만 사용 가능 | [O] `paradedb.score(id)` 명시적 함수 |
| **BM25 파라미터** | [O] k1=1.2, b=0.75 (코드 수정 가능) | [X] 고정값 (SQL 튜닝 불가) | [X] 고정값 (SQL 튜닝 불가) |
| **설치 방법** | [O] 추가 확장 불필요 | [WARN] 소스 컴파일 필요 | [WARN] 별도 Docker 이미지 필요 |
| **Docker** | `pgvector/pgvector:pg18` | `pgvector/pgvector:pg18` + 빌드 | `paradedb/paradedb:latest` (port 5433) |
| **pg_dump 호환** | [O] 완전 | [O] 완전 | [WARN] Tantivy 파일 별도 처리 필요 |

---

## 2. DDL 패턴 비교

### pl/pgsql custom BM25

```sql
-- 역색인 테이블 + 통계 테이블 + 빌드/검색 함수를 모두 직접 생성
CREATE TABLE bm25idx_{table} (
    term    TEXT NOT NULL,
    doc_id  TEXT NOT NULL,
    tf      FLOAT NOT NULL,
    PRIMARY KEY (term, doc_id)
);
CREATE INDEX bm25idx_{table}_term_idx ON bm25idx_{table}(term);
CREATE TABLE bm25stats_{table} (total_docs INT, avg_doc_len FLOAT);

-- 인덱싱: 함수 호출 1번으로 전체 테이블 색인
SELECT bm25_build_{table}('source_table');

-- 검색
SELECT doc_id, score FROM bm25_search_{table}('쿼리 텍스트', 10);
```

### pg_textsearch (Timescale)

```sql
-- text_config에 schema prefix 필수 (pg_catalog만 탐색하므로)
CREATE INDEX {idx} ON {table}
USING bm25(text)
WITH (text_config='public.korean');

-- 검색: <@> 연산자, 내부에서 자동 토크나이징
SELECT id FROM {table}
ORDER BY text <@> '쿼리 텍스트'
LIMIT 10;
```

### ParadeDB pg_search

```sql
-- key_field 필수 (paradedb.score(id) 에서 참조)
CREATE INDEX {table}_bm25_idx ON {table}
USING bm25(id, text)
WITH (key_field='id');

-- 검색: field:term OR 형식 쿼리 직접 구성, score 명시적 호출
SELECT id, paradedb.score(id)
FROM {table}
WHERE {table} @@@ 'text:한국 OR text:경제 OR text:발전'
ORDER BY paradedb.score(id) DESC
LIMIT 10;
```

### pl/pgsql BM25 빌드 함수 핵심 변경 (regex → tsvector)

```sql
-- 신규: unnest(to_tsvector('{ts_config}', rec.text)) 로 토크나이징
-- ts_config='pg_catalog.simple' → 기본 whitespace 분리
-- ts_config='public.korean'     → MeCab mecab-ko-dic 형태소 분석

INSERT INTO {idx_table}(term, doc_id, tf)
SELECT lexeme, rec.id, array_length(positions, 1)::float
FROM unnest(to_tsvector('{ts_config}', rec.text))
ON CONFLICT (term, doc_id) DO UPDATE SET tf = EXCLUDED.tf;

-- 쿼리 토크나이징도 동일한 config:
q_terms := ARRAY(SELECT lexeme FROM unnest(to_tsvector('{ts_config}', query)));
```

이 변경으로 pl/pgsql BM25가 MeCab 형태소 분석을 순수 SQL로 적용할 수 있게 됨.
`tsvector`의 `positions` 배열 길이 = TF, `lexeme` = 기본형.

### DDL 비교 요약

| 항목 | pl/pgsql | pg_textsearch | ParadeDB |
|------|----------|---------------|---------|
| 인덱스 생성 방식 | 함수 호출 | `USING bm25(col) WITH (...)` | `USING bm25(id, col) WITH (key_field=...)` |
| 쿼리 구성 | `fn_search(text, k)` 함수 호출 | `ORDER BY col <@> text` | `WHERE t @@@ 'field:term OR ...'` |
| 토크나이저 지정 | `ts_config` 파라미터 (`'public.korean'` 등) | `text_config='schema.config'` | N/A (lindera 고정) |
| 점수 획득 | RETURNS TABLE의 score 컬럼 | 불가 (ORDER BY 내재) | `paradedb.score(id)` |

---

## 3. BM25 알고리즘 구현 비교

세 구현 모두 동일한 BM25 공식을 사용하지만 내부 구현 방식이 크게 다르다:

```
score(q, d) = sum_t [ IDF(t) * (tf(t,d) * (k1+1)) / (tf(t,d) + k1*(1-b+b*|d|/avgdl)) ]
IDF(t) = ln(1 + (N - df(t) + 0.5) / (df(t) + 0.5))
k1=1.2, b=0.75 (모두 동일)
```

### 실행 방식 차이

| 단계 | pl/pgsql | pg_textsearch | ParadeDB |
|------|----------|---------------|---------|
| **후보 수집** | `WHERE term = ANY(q_terms)` 풀스캔 | WAND: block-max upper bound으로 조기 skip | Tantivy: segment 병렬 탐색 |
| **doc_len 계산** | `SELECT doc_id, SUM(tf) GROUP BY doc_id` (쿼리마다 재계산) | GIN 인덱스 내 사전 계산 | Tantivy `.fieldnorm` segment 파일 |
| **IDF** | 쿼리마다 `COUNT(DISTINCT doc_id)` | 인덱스 통계 사전 계산 | Tantivy segment 통계 |
| **병렬성** | [X] 단일 스레드 | [O] PostgreSQL 병렬 쿼리 가능 | [O] Rust rayon (segment 병렬) |

### pl/pgsql의 쿼리 비용 문제

pl/pgsql search function(`bm25_search_*`)의 CTE는 매 쿼리마다 전체 `doc_len` 서브쿼리를 실행한다:

```sql
-- 매 검색마다 전체 역색인 스캔
JOIN (
    SELECT dl2.doc_id, SUM(dl2.tf) AS doc_len
    FROM bm25idx_{table} dl2 GROUP BY dl2.doc_id
) dl ON dl.doc_id = i.doc_id
```

10k doc에서 term 인덱스(`term_idx`)가 있어도 doc_len 집계가 O(전체 역색인 크기)이다.
pg_textsearch/ParadeDB는 doc_len을 인덱스 빌드 시점에 저장하므로 이 비용이 없다.

---

## 4. 토크나이저 품질 비교

토크나이저는 세 구현의 성능 차이를 가장 크게 결정하는 요인이다.

### 기능 비교

| 기능 | pl/pgsql+simple | pl/pgsql+public.korean | ts_mecab_ko (C/MeCab) | lindera (Rust) |
|------|----------------|------------------------|----------------------|----------------|
| 형태소 분석 | [X] 없음 | [O] MeCab POS 태깅 | [O] POS 태깅 14종 | [WARN] 기본 분절 |
| 기본형 추출 (lemmatization) | [X] | [O] mecab-ko-dic field 3 | [O] mecab-ko-dic field 3 | [X] |
| 조사 제거 (JX, JKS...) | [X] | [O] 품사 필터 | [O] 품사 필터 | [WARN] 불완전 |
| 어미 제거 (EF, EC...) | [X] | [O] VV/VA 기본형만 색인 | [O] VV/VA 기본형만 색인 | [X] |
| 복합명사 분해 | [X] | [O] `compound_lexemes()` | [O] `compound_lexemes()` | [X] |
| 하다 동사 처리 | [X] | [O] `ends_with_hada()` | [O] `ends_with_hada()` | [X] |
| 한자 변환 | [X] | [O] `hanja2hangul()` | [O] `hanja2hangul()` | [X] |
| 사전 품질 | N/A | mecab-ko-dic (Sejong 기반) | mecab-ko-dic (Sejong 기반) | KCC (제한적) |

`pl/pgsql+public.korean`과 `ts_mecab_ko`는 동일한 MeCab C extension을 사용 — 토크나이저 품질이 동일하다.
차이는 랭킹 알고리즘: pl/pgsql은 WAND 없는 풀스캔 BM25, pg_textsearch는 Block-Max WAND BM25.

### 토크나이징 예시

```
입력: "경제 발전이 이루어졌다"

pl/pgsql+simple:  ['경제', '발전이', '이루어졌다']  -- pg_catalog.simple: whitespace 분리만
pl/pgsql+korean:  ['경제', '발전', '이루']           -- public.korean: MeCab 기본형 추출
ts_mecab_ko:      ['경제', '발전', '이루어지다']     -- 동일 MeCab, pg_textsearch 인덱스
lindera:          ['경제', '발전이', '이루어지다']   -- 조사 미제거 ('이' 포함)
                              ↑ 조사 '이' 잔류 → 쿼리 '발전'과 미매칭
```

---

## 5. 성능 비교 (MIRACL-ko, 10k corpus, 213 queries)

| 지표 | pl/pgsql+simple | pl/pgsql+MeCab | pg_textsearch+MeCab | ParadeDB+lindera |
|------|----------------|----------------|---------------------|-----------------|
| **NDCG@10** | 0.4071 | **0.6412** | 0.3374 | 0.2275 |
| **Recall@10** | 0.4757 | **0.8012** | 0.3844 | 0.2605 |
| **MRR** | 0.4580 | **0.6191** | 0.4133 | 0.3024 |
| **p50 latency** | 11.14ms | 10.44ms | **0.86ms** | 2.27ms |
| **p95 latency** | — | — | **1.66ms** | 5.81ms |
| **Zero-result %** | 1.0% | **0.0%** | 0.5% | 5.6% |
| **Build time (10k)** | 86.6s | 37.1s | ~5s | ~0.1s |
| **term-doc pairs** | 503,852 | 456,039 | N/A | N/A |

`pl/pgsql+MeCab` = `ts_config='public.korean'` — NDCG@10=0.6412으로 pgvector-sparse BM25 (kiwi-cong) 0.6326을 능가함.
`pl/pgsql+simple` = `ts_config='pg_catalog.simple'` — tsvector 기본 분리만 적용.

### EZIS (97 docs, 131 queries)

| 지표 | pl/pgsql+simple | pl/pgsql+MeCab | pg_textsearch+MeCab | ParadeDB+lindera |
|------|----------------|----------------|---------------------|-----------------|
| **NDCG@10** | 0.8567 | **0.9290** | 0.8417 | 0.7196 |
| **Recall@10** | 0.9733 | **0.9924** | 0.9008 | 0.9046 |
| **MRR** | 0.8237 | **0.9085** | 0.8224 | 0.6621 |
| **p50 latency** | 1.64ms | 1.72ms | 3.6ms | **0.94ms** |

`pl/pgsql+MeCab` (0.9290)이 EZIS에서 pl/pgsql 변형 중 최고, 전체 2위 (Bayesian BM25+BGE-M3 dense 0.9493 다음).
순수 BM25 방법 중 최고이며, pg_textsearch(0.8417)보다 +10.4% NDCG 우위.
ParadeDB latency 우위: 97 doc = 단일 Tantivy segment, FFI 오버헤드 감소.

---

## 6. 알고리즘 복잡도 분석

### 인덱스 빌드 시간

| 구현 | 복잡도 | 1000 docs 실측 | 10k docs 예상 |
|------|--------|----------------|--------------|
| pl/pgsql (수정 후) | O(N * T) | 27.3s | ~270s (linear) |
| pl/pgsql (수정 전, JSONB 버그) | O(N * T^2) | timeout | timeout |
| pg_textsearch | O(N * T * log N) | ~5s (GIN 빌드) | ~60s |
| ParadeDB | O(N * T + segment merge) | ~0.1s | ~1s |

T = 평균 토큰 수/문서 (~62)

### 검색 시간

| 구현 | 복잡도 | 특성 |
|------|--------|------|
| pl/pgsql | O(|idx| + Q) | doc_len 전체 집계 + term 룩업 |
| pg_textsearch | O(k * log |V| + WAND skip) | WAND: top-k 이외 skip |
| ParadeDB | O(S * k * log |seg|) | S = segment 수, FFI 오버헤드 고정 |

---

## 7. 코드 복잡도 비교

### 구현 코드량 (phase2_tsvector_comparison.py)

| 구현 | 코드 라인 | 의존성 |
|------|-----------|--------|
| pl/pgsql | lines 454-543 (90 lines SQL + 30 lines Python) | psycopg2만 |
| pg_textsearch | lines 391-448 (58 lines Python) | pg_textsearch extension + textsearch_ko |
| ParadeDB | lines 221-282 (62 lines Python) | paradedb Docker image |

### 유지보수 난이도

| 항목 | pl/pgsql | pg_textsearch | ParadeDB |
|------|----------|---------------|---------|
| 토크나이저 교체 | [O] 코드 수정만 | [O] text_config 변경 | [X] 불가 |
| BM25 파라미터 튜닝 | [O] SQL 상수 변경 | [X] extension 재컴파일 | [X] |
| 스코어 접근 | [O] RETURNS TABLE | [X] ORDER BY만 | [O] paradedb.score() |
| 디버깅 용이성 | [O] 테이블 직접 조회 | [X] 블랙박스 | [WARN] Tantivy 로그 |
| 확장성 (커스텀 로직) | [O] SQL로 직접 | [X] C extension 필요 | [X] Rust 수정 필요 |

---

## 8. 버그 및 제약 요약

### pl/pgsql custom BM25

1. **JSONB O(n^2) 버그** (수정됨):
   - 원인: `jsonb_set()` 루프 → 토큰당 새 JSONB 객체 할당
   - 수정: `INSERT ... SELECT unnest(tok_arr) t GROUP BY t`
   - 효과: O(T^2) → O(T) per document

2. **AmbiguousColumn 버그** (수정됨):
   - 원인: `RETURNS TABLE(doc_id TEXT, ...)` 선언이 PL/pgSQL scope에 `doc_id` 변수 생성
   - 수정: CTE 서브쿼리에 테이블 별칭 명시 (`ix.doc_id`, `dl2.doc_id`)

3. **구조적 한계**:
   - regex tokenizer → 어미/조사 미제거 → 낮은 recall
   - 쿼리마다 `doc_len` 전체 집계 → 대규모 코퍼스에서 느림
   - WAND 없음 → top-k 이외 후보도 모두 평가

### pg_textsearch

1. **schema prefix gotcha**: `text_config='korean'` 실패, `'public.korean'` 필요
2. **점수 불투명**: `<@>` 결과를 SELECT에 노출 불가
3. **소스 컴파일 필요**: 공식 Docker 이미지에 미포함

### ParadeDB

1. **5-term 하드 제한**: 긴 쿼리는 앞 5개 텀만 평가 (벤치마크 코드 제약)
2. **lindera 한계**: 조사/어미 미제거 → MIRACL Zero-result 5.6%
3. **토크나이저 교체 불가**: MeCab 대체 방법 없음
4. **별도 컨테이너**: pgvector 이미지와 공존 불가

---

## 9. 선택 가이드

| 요구사항 | 권장 구현 | 이유 |
|----------|-----------|------|
| 한국어 형태소 분석 + 최고 품질 | **pg_textsearch + MeCab** | NDCG+48% vs ParadeDB, 어미/조사 처리 |
| 서브밀리초 레이턴시 | **pg_textsearch** | WAND, C-native, 0.86ms p50 |
| 점수 노출 필요 (reranking) | **ParadeDB** | `paradedb.score(id)` |
| 추가 확장 없이 BM25 | **pl/pgsql** (개발/소규모) | psycopg2만 필요, 하지만 한계 명확 |
| BM25 파라미터 튜닝 | **pl/pgsql** | k1, b를 SQL 상수로 직접 수정 가능 |
| ES/Lucene 친숙 팀 | **ParadeDB** | field:term query syntax |
| Production (한국어) | **pg_textsearch** | 품질 + 속도 + PG 생태계 통합 |
