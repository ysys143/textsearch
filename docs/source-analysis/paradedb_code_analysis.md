# ParadeDB pg_search (Tantivy + lindera) — 코드 레벨 분석

> 대상: ParadeDB `pg_search` extension (Tantivy BM25 + lindera Korean)
> 실험: Phase 2, Method 2-D (`ParadeDB pg_search`)
> 소스: `experiments/phase2_tsvector/phase2_tsvector_comparison.py` lines 221-282
> Docker: `paradedb/paradedb:latest` at port 5433

---

## 1. 아키텍처 개요

ParadeDB pg_search는 Rust로 작성된 Tantivy 전문 검색 라이브러리를 pgrx(PostgreSQL extension for Rust) 바인딩을 통해 PostgreSQL extension으로 감싼 것이다. Lucene 계열 segment 기반 역색인 구조를 PostgreSQL 내에서 동작시킨다.

```
쿼리 텍스트 (Lucene-style query string)
    -> 파싱: "text:term1 OR text:term2"
    -> lindera 토크나이저 (Rust, 내장)
    -> Tantivy 역색인 탐색 (segment-based)
    -> BM25 스코어 계산
    -> paradedb.score(id) 로 PostgreSQL 레이어에 반환
    -> ORDER BY score DESC
    -> 결과 반환
```

### 핵심 특징

| 속성 | 값 |
|------|-----|
| 알고리즘 | Tantivy BM25 (Lucene 유사) |
| 백엔드 | Rust (pgrx 바인딩) |
| 인덱스 구조 | Tantivy segment (PG 외부 파일 시스템) |
| 쿼리 연산자 | `@@@` (triple-at match operator) |
| 스코어 노출 | `paradedb.score(id)` 명시적 함수 |
| 토크나이저 | 내장 lindera (교체 불가, 컴파일 시 고정) |
| 쿼리 언어 | Field-scoped Lucene query string |
| Docker | `paradedb/paradedb:latest` (port 5433, 별도 컨테이너) |

---

## 2. DDL 패턴

### 테이블 + 인덱스 생성 (`phase2_tsvector_comparison.py:224`)

```python
def setup_paradedb(conn, table: str, docs: List[Dict]) -> bool:
    # 1. 테이블 생성
    cur.execute(f"""
        CREATE TABLE {table} (
            id TEXT PRIMARY KEY,
            text TEXT NOT NULL
        )
    """)

    # 2. 문서 삽입
    psycopg2.extras.execute_values(
        cur, f"INSERT INTO {table} (id, text) VALUES %s",
        [(str(d["id"]), d["text"]) for d in docs],
        page_size=500
    )

    # 3. BM25 인덱스 생성
    cur.execute(f"""
        CREATE INDEX {table}_bm25_idx ON {table}
        USING bm25(id, text)
        WITH (key_field='id')
    """)
```

### DDL 주요 파라미터

| 파라미터 | 의미 |
|----------|------|
| `USING bm25(id, text)` | BM25 인덱스 타입, 색인할 컬럼 나열 |
| `key_field='id'` | 문서 ID 컬럼 지정 (필수) — `paradedb.score(id)` 에서 참조 |

pg_textsearch의 `WITH (text_config='...')` 와 달리 토크나이저를 SQL로 지정할 수 없다.
`korean_lindera`는 컴파일 시 결정된 기본 한국어 토크나이저.

---

## 3. 검색 쿼리

### 쿼리 구성 (`phase2_tsvector_comparison.py:261`)

```python
def search_paradedb(conn, query_text: str, table: str, k: int = 10) -> List[str]:
    # 공백 분리 후 최대 5개 텀만 사용
    terms = query_text.split()[:5]
    if not terms:
        return []

    # Field-scoped OR query 구성
    query_parts = " OR ".join(f"text:{t}" for t in terms)
    # 예: "text:한국 OR text:경제 OR text:발전"

    cur.execute(f"""
        SELECT id, paradedb.score(id)
        FROM {table}
        WHERE {table} @@@ %s
        ORDER BY paradedb.score(id) DESC
        LIMIT %s
    """, (query_parts, k))
```

### 쿼리 언어 특성

- `@@@` 연산자: Tantivy BM25 match operator
- `text:term` 형식: field-scoped term query
- `OR` 조합: 부분 매칭 허용 (AND보다 높은 recall)
- `paradedb.score(id)`: ID로 Tantivy side-channel에서 점수 조회 (별도 호출 필요)
- 5-term 제한: 벤치마크 코드의 하드코딩 — 긴 쿼리는 앞 5개 텀만 평가

### pg_textsearch `<@>` 와 비교

```sql
-- pg_textsearch: 쿼리 텍스트 직접 전달, 내부에서 토크나이징
SELECT id FROM t ORDER BY text <@> '한국 경제 발전' LIMIT 10

-- ParadeDB: field:term OR 형식으로 명시적 구성 필요
SELECT id, paradedb.score(id) FROM t
WHERE t @@@ 'text:한국 OR text:경제 OR text:발전'
ORDER BY paradedb.score(id) DESC LIMIT 10
```

ParadeDB는 쿼리 구성 로직이 클라이언트에 노출되고, score 함수를 명시적으로 호출해야 한다.

---

## 4. Tantivy 아키텍처

### Segment 기반 역색인

Tantivy는 Lucene의 segment-merge 아키텍처를 Rust로 구현한 것이다:

```
인덱스 생성:
  문서들 -> 메모리 버퍼 -> segment 파일들 (OS filesystem)
  segments: .idx (posting list), .term (term dict), .fieldnorm (doc lengths)
  백그라운드 merge: 작은 segment들을 주기적으로 큰 segment로 병합

검색:
  쿼리 -> 모든 segment에 대해 병렬 탐색
  각 segment의 partial BM25 -> 최종 merge -> top-k 반환
```

### BM25 구현

Tantivy의 BM25: `k1=1.2, b=0.75` (기본값, SQL로 변경 불가)

```
score(q, d) = sum_t [
    IDF(t) * (tf(t,d) * (k1+1)) / (tf(t,d) + k1*(1-b+b*|d|/avgdl))
]

IDF(t) = ln(1 + (N - df(t) + 0.5) / (df(t) + 0.5))
```

### PostgreSQL<->Rust FFI 오버헤드

```
쿼리 요청 (SQL)
    -> PostgreSQL -> pgrx FFI boundary
    -> Tantivy (Rust heap)
    -> 검색 결과 (Rust Vec)
    -> pgrx: Rust Vec -> PostgreSQL Datum array 변환
    -> PostgreSQL 레이어 반환
```

이 FFI 변환이 매 쿼리마다 발생하므로 C extension 대비 오버헤드가 있다.
벤치마크에서 pg_textsearch(0.86ms) 대비 ParadeDB(2.27ms)가 2.6배 느린 주요 원인.

---

## 5. lindera 한국어 토크나이저 분석

### lindera 개요

lindera는 일본어 형태소 분석기 MeCab의 Rust 포팅으로 시작했다.
한국어 지원은 후에 추가되었으나 일본어 중심 설계다.

### MeCab(ts_mecab_ko) vs lindera 비교

| 기능 | ts_mecab_ko (C, mecab-ko-dic) | lindera (Rust) |
|------|-------------------------------|----------------|
| 기본형 추출 (lemmatization) | [O] field 3 (MECAB_BASIC) | [X] 미적용 |
| 복합명사 분해 | [O] `compound_lexemes()` | [X] |
| 하다 동사 어근 추출 | [O] `ends_with_hada()` | [X] |
| 조사 제거 | [O] 품사 필터 (JX, JKS 등 제외) | [WARN] 불완전 |
| 어미 제거 | [O] VV/VA 기본형만 색인 | [X] |
| 외래어(SL) 처리 | [O] accept_parts_of_speech 포함 | [WARN] |
| 한자 변환 | [O] `hanja2hangul()` | [X] |
| 사전 품질 | mecab-ko-dic (Sejong 기반) | KCC (제한적) |

### 결과에 미치는 영향

```
쿼리: "경제 발전이 이루어지다"
  ts_mecab_ko 토크나이징: ['경제', '발전', '이루어지다' -> '이루어지']
  lindera 토크나이징:     ['경제', '발전이', '이루어지다']
                            ↑ 조사 '이' 미제거   ↑ 어미 미처리

색인 텀 "발전이" vs 쿼리 텀 "발전" -> 미매칭 -> recall 손실
```

이것이 MIRACL에서 Zero-result rate가 5.6%에 달하는 근본 원인:
조사/어미가 붙은 채로 색인되어 기본형 쿼리와 매칭 실패.

---

## 6. 성능 분석

### MIRACL-ko (1000 docs, 213 queries)

| 지표 | ParadeDB+lindera | pg_textsearch+MeCab | 차이 |
|------|-----------------|---------------------|------|
| NDCG@10 | 0.2275 | 0.3374 | -32% |
| Recall@10 | 0.2605 | 0.3844 | -32% |
| MRR | 0.3024 | 0.4133 | -27% |
| p50 latency | 2.27ms | 0.86ms | +164% |
| p95 latency | 5.81ms | 1.66ms | +250% |
| Zero-result % | 5.6% | 0.5% | +1020% |

### EZIS (97 docs, 131 queries)

| 지표 | ParadeDB+lindera | pg_textsearch+MeCab |
|------|-----------------|---------------------|
| NDCG@10 | 0.7196 | 0.8417 |
| Recall@10 | 0.9046 | 0.9008 |
| MRR | 0.6621 | 0.8224 |
| p50 latency | 0.94ms | 3.6ms |

EZIS에서는 p50 latency가 ParadeDB(0.94ms)가 더 빠르다.
97개 소규모 코퍼스에서 Tantivy가 단일 segment에 올라가 FFI 오버헤드가 WAND 이점을 상쇄.

---

## 7. 발견된 문제 및 제약

### 벤치마크에서 발견된 이슈

1. **5-term 하드 제한** (`phase2_tsvector_comparison.py:265`):
   ```python
   terms = query_text.split()[:5]
   ```
   긴 한국어 쿼리(6+ 어절)에서 마지막 텀들이 잘려 precision 손실.

2. **OR-only 검색**: AND/phrase/proximity search 미구현.
   Tantivy는 지원하지만 벤치마크 코드에서 OR만 사용.

3. **schema_bm25() 오류**: `paradedb.schema_bm25()` 함수가 없다는 에러 발생
   (ParadeDB 버전 불일치, `paradedb/paradedb:latest` 이미지 업데이트 이슈).

4. **토크나이저 교체 불가**: `text_config` 같은 플러그인 포인트가 없어
   MeCab이나 kiwipiepy로 토크나이저를 교체할 방법이 없다.

### 아키텍처 제약

- **별도 컨테이너 필요**: `pgvector/pgvector:pg18` 이미지에서 사용 불가.
  `paradedb/paradedb:latest` 전용 이미지만 지원.
- **인덱스 파일 분리**: Tantivy 인덱스가 PG tablespace 밖에 저장되어
  `pg_dump/pg_restore`로 완전 백업이 안 될 수 있다.
- **BM25 파라미터 비노출**: k1, b를 SQL로 변경 불가.

---

## 8. 사용 권장 시나리오

| 시나리오 | 적합도 |
|----------|--------|
| 영어/비형태소 언어 BM25 | [O] Tantivy 품질 우수 |
| ES/Lucene 쿼리 문법 익숙한 팀 | [O] query string 친숙 |
| 한국어 형태소 분석 필요 | [X] lindera 한계 |
| 서브밀리초 레이턴시 | [X] FFI 오버헤드 |
| 점수 노출 (reranking용) | [O] `paradedb.score(id)` |
| 기존 PG 인프라 통합 | [WARN] 별도 컨테이너 필요 |
