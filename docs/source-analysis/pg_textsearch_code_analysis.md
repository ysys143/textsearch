# pg_textsearch (Timescale) — 코드 레벨 분석

> 대상: Timescale `pg_textsearch` extension (Block-Max WAND BM25)
> 실험: Phase 2, Method 2-H-a (`pg_textsearch + public.korean`)
> 소스: `experiments/phase2_tsvector/phase2_tsvector_comparison.py` lines 391-448

---

## 1. 아키텍처 개요

pg_textsearch는 PostgreSQL의 기존 text search 인프라(tsvector / GIN 인덱스) 위에
**Block-Max WAND (Weak AND) BM25** 알고리즘을 얹은 C extension이다.
PostgreSQL 내부의 posting list를 그대로 활용하므로 별도의 외부 인덱스 파일 없이 동작한다.

```
쿼리 텍스트
    -> (text_config로 지정된 text search configuration 사용)
토크나이저 (MeCab, bigram 등)
    ->
tsvector 변환
    ->
GIN 인덱스 탐색 (Block-Max WAND: 조기 종료)
    ->
BM25 스코어 계산 및 정렬
    ->
결과 반환
```

### 핵심 특징

| 속성 | 값 |
|------|-----|
| 알고리즘 | Block-Max WAND BM25 |
| 백엔드 | C extension (PostgreSQL-native) |
| 인덱스 구조 | tsvector posting list (PG 내부, GIN) |
| 쿼리 연산자 | `<@>` distance operator |
| 토크나이저 | 플러그형 (PostgreSQL text search config 참조) |
| 스코어 노출 | 암묵적 (ORDER BY에 내장, SELECT 노출 불가) |
| 외부 프로세스 | 없음 — 단일 PostgreSQL 프로세스 내 처리 |

---

## 2. DDL 패턴

### 인덱스 생성

```sql
-- text_config는 schema prefix 필수 (아래 gotcha 참조)
CREATE INDEX {idx_name} ON {table}
USING bm25(text)
WITH (text_config='public.korean');
```

실험 코드 (`phase2_tsvector_comparison.py:421`):

```python
cur.execute(f"""
    CREATE INDEX {idx_name} ON {table}
    USING bm25(text) WITH (text_config='{ts_config}')
""")
```

text_config 검증 코드 (`phase2_tsvector_comparison.py:409`):

```python
schema, cfgname = (ts_config.split(".", 1) if "." in ts_config
                   else ("pg_catalog", ts_config))
cur.execute(
    "SELECT 1 FROM pg_ts_config c JOIN pg_namespace n ON n.oid=c.cfgnamespace "
    "WHERE n.nspname=%s AND c.cfgname=%s LIMIT 1",
    (schema, cfgname)
)
```

### 검색 쿼리

```sql
SELECT id
FROM {table}
ORDER BY text <@> %s   -- %s: 검색어 (raw text, 내부에서 토크나이징)
LIMIT %s
```

`<@>` 연산자는 값이 작을수록 관련도가 높은 거리 메트릭으로 작동한다
(ORDER BY ASC가 기본). `paradedb.score()` 같은 별도 점수 함수 없이 순위 정렬이 가능하다.

---

## 3. text search configuration 설정

`phase2_textsearch_ko_ab.py:86` 기준 MeCab config 등록:

```sql
CREATE EXTENSION IF NOT EXISTS pg_textsearch;

CREATE TEXT SEARCH DICTIONARY public.korean_stem (
    TEMPLATE = mecabko           -- textsearch_ko C extension에서 제공
);

CREATE TEXT SEARCH CONFIGURATION public.korean (
    PARSER = public.korean       -- textsearch_ko 파서
);

-- 한국어 토큰 타입 -> MeCab stemmer 매핑
ALTER TEXT SEARCH CONFIGURATION public.korean
    ADD MAPPING FOR word, hword, hword_part
    WITH public.korean_stem;

-- ASCII 토큰 타입 -> 영어 stemmer 매핑
ALTER TEXT SEARCH CONFIGURATION public.korean
    ADD MAPPING FOR asciihword, asciiword, hword_asciipart
    WITH english_stem;
```

---

## 4. textsearch_ko C extension 분석

위 config의 `PARSER = public.korean`을 제공하는 `vendor/textsearch_ko/ts_mecab_ko.c`를 분석한다.

### 구조 개요

```
ts_mecabko_start()    -> mecab_new() + normalize() 호출, 입력 정규화
ts_mecabko_gettoken() -> mecab_sparse_tonode2() 토큰 순회
ts_mecabko_end()      -> 메모리 해제
ts_mecabko_lexize()   -> 형태소 기본형(MECAB_BASIC, field 3) 추출
```

### 품사 필터 (`accept_parts_of_speech`)

```c
/* ts_mecab_ko.c:102 */
static char *accept_parts_of_speech[14] = {
    "NNG", "NNP", "NNB", "NNBC", "NR",   // 일반명사, 고유명사, 의존명사, 단위명사, 수사
    "VV", "VA",                            // 동사, 형용사 (기본형)
    "MM", "MAG",                           // 관형사, 부사
    "XSN", "XR",                           // 접미사, 어근
    "SH", "SL",                            // 한자, 외래어 (SL: 2-C 개선 추가)
    ""
};
```

`NNG`, `NNP` (명사류)와 `VV`, `VA` (용언 기본형)만 색인.
조사(`JX`, `JKS` 등)와 어미(`EF`, `EC` 등)는 색인 대상에서 제외된다.

### 기본형 추출 (lemmatization)

```c
/* MECAB_BASIC = 3: mecab-ko-dic CSV의 3번째 필드 = 기본형 */
#define MECAB_BASIC    3

feature(node, MECAB_BASIC, &base, &baselen)
// 예: "가다" -> base="가다", "먹었다" -> base="먹다"
```

### 복합명사 분해 및 하다 동사

```c
/* ts_mecab_ko.c:94 */
static bool has_hangul(const char *s, int len);
static bool ends_with_hada(const char *s, int slen);
static TSLexeme *compound_lexemes(const char *base, int baselen,
                                  const char *compound, int complen);
```

`NNG` 복합명사(예: "데이터베이스검색")를 분해하고,
`VV+XSN` 하다 동사(예: "분석하다")의 어근을 추출한다.

### 한자 정규화

```c
PG_FUNCTION_INFO_V1(hanja2hangul);  // 한자 -> 한글 변환
```

### ts_mecab_ko vs lindera 비교

| 기능 | ts_mecab_ko (C) | lindera (Rust) |
|------|----------------|----------------|
| 품사 태깅 | NNG/NNP/VV/VA 등 14종 | 기본 분절 |
| 기본형 추출 | mecab-ko-dic field 3 | 미적용 |
| 복합명사 분해 | [O] `compound_lexemes()` | [X] |
| 하다 동사 처리 | [O] `ends_with_hada()` | [X] |
| 한자 변환 | [O] `hanja2hangul()` | [X] |
| 조사/어미 제거 | [O] 품사 필터 | [X] |

---

## 5. Block-Max WAND 알고리즘

WAND (Weak AND)는 BM25 early termination 알고리즘이다.
각 posting list에 "block max score" (블록 내 최대 BM25 기여도)를 저장하고,
현재 top-k threshold보다 기여 불가능한 후보를 조기에 건너뛴다.

```
Block-Max WAND 동작:
1. 쿼리 텀들의 IDF 계산 -> 내림차순 정렬
2. "pivot" 선택: 누적 upper bound이 threshold를 넘는 최초 텀
3. pivot보다 앞선 후보는 skip -> posting list pointer 전진
4. 실제 BM25 계산은 pivot 이후 후보에만 수행
5. Top-k가 찰 때까지 반복

장점: 전체 코퍼스 스캔 없이 top-k 후보만 평가 -> 저지연
```

이것이 pg_textsearch가 0.86ms p50를 달성하는 핵심 이유:
tsvector GIN 인덱스의 skip-scan과 WAND가 결합된다.

---

## 6. 핵심 gotcha: schema prefix 필수

`docs/plan/phase2_tsvector_korean.md:116`:

```
pg_textsearch 인덱스 빌더가 session search_path를 무시하고 pg_catalog만 탐색함.
```

```python
# [O] 작동
"WITH (text_config='public.korean')"

# [X] 실패: pg_catalog에서 'korean' config 못 찾음
"WITH (text_config='korean')"
```

회피 방법: text_config 값에 항상 schema prefix(`public.`)를 붙인다.

---

## 7. 성능 분석

### MIRACL-ko (1000 docs, 213 queries)

| 지표 | pg_textsearch+MeCab | ts_rank_cd+MeCab | ParadeDB+lindera |
|------|--------------------|-----------------|--------------------|
| NDCG@10 | **0.3374** | 0.1815 | 0.2275 |
| Recall@10 | **0.3844** | 0.1752 | 0.2605 |
| MRR | **0.4133** | 0.2432 | 0.3024 |
| p50 latency | **0.86ms** | 0.73ms | 2.27ms |
| p95 latency | **1.66ms** | — | 5.81ms |
| Zero-result % | **0.5%** | 64.3% | 5.6% |

**BM25 vs ts_rank_cd**: 같은 MeCab 토크나이저에서 BM25가 NDCG +86%.
`ts_rank_cd`는 AND 필터 기반 -> 쿼리 텀 중 하나라도 없으면 결과 0건(64.3% zero-result).
BM25 WAND는 OR 기반 union -> 부분 매칭도 스코어링.

### EZIS (97 docs, 131 queries)

| 지표 | pg_textsearch+MeCab |
|------|---------------------|
| NDCG@10 | 0.8417 |
| Recall@10 | 0.9008 |
| MRR | 0.8224 |
| p50 latency | 3.6ms |

소규모 코퍼스에서는 WAND의 skip 동작이 작은 posting list에서 오버헤드로 작용한다.

---

## 8. 제약 사항

1. **소스 컴파일 필요**: `pgvector/pgvector:pg18` Docker 이미지에 미포함.
   직접 빌드하거나 Timescale 공식 이미지 사용 필요.

2. **BM25 파라미터 비노출**: k1, b 값을 SQL로 튜닝할 수 없음. 기본값(k1=1.2, b=0.75) 고정.

3. **스코어 불투명**: `<@>` 결과는 ORDER BY에만 사용 가능.
   수치 점수를 SELECT에 노출하려면 extension 패치 필요.

4. **쿼리 전처리 불가**: 쿼리 텍스트가 인덱스의 text_config로 그대로 토크나이징됨.
   쿼리 확장, 동의어 처리 등 SQL 레벨 개입이 어렵다.

---

## 9. 사용 권장 시나리오

| 시나리오 | 적합도 |
|----------|--------|
| 한국어 형태소 분석 FTS (PostgreSQL 내) | [O] 최적 |
| 서브밀리초 ~ 1ms 레이턴시 요구 | [O] |
| BM25 파라미터 튜닝 필요 | [X] 제한적 |
| 점수 노출 + 후처리 필요 | [X] 불편 |
| 다국어 동시 검색 | [WARN] config별 인덱스 분리 필요 |
