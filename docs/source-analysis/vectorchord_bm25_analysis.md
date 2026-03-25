# VectorChord-BM25 및 pg_tokenizer.rs 소스 분석

**작성일**: 2026-03-25
**버전**: v0.3.0 (VectorChord-BM25) / v0.1.1 (pg_tokenizer.rs)
**목적**: Phase 6 - textsearch_ko와 VectorChord-BM25 연결 가능성 검증

---

## 1. VectorChord-BM25 아키텍처

### 1.1 데이터 타입

| 속성 | 상세 |
|------|------|
| **Vector Type** | `bm25vector` |
| **구조** | `{token_id: frequency}` 희소벡터 (sparse vector) |
| **저장 형식** | 원본 토큰 개수 (raw token counts) |
| **예** | `'{1:2, 3:1, 5:4}'::bm25vector` (token_id=1이 2회, 3이 1회, 5가 4회) |

### 1.2 인덱스 구조

```sql
CREATE INDEX idx_name ON table_name USING bm25 (embedding bm25_ops);
```

| 구성요소 | 설명 |
|---------|------|
| **Index Type** | Block-WeakAnd posting list |
| **IDF 계산** | 쿼리 시간에 계산 (query-time) |
| **통계 기반** | 인덱스 메타데이터에서 문서 빈도 추출 |
| **메모리 효율** | pl/pgsql v2 대비 순차 I/O 활용 |

### 1.3 검색 연산자

| 연산자 | 반환값 | 의미 |
|--------|--------|------|
| `<&>` | 음수 BM25 점수 | 낮을수록 관련성 높음 |
| 사용법 | `ORDER BY embedding <&> query_vec` | 관련성순 정렬 |

### 1.4 쿼리 생성 방식

```sql
-- 쿼리 벡터 생성
to_bm25query('index_name', bm25vector) -> bm25query

-- 전체 검색 패턴
SELECT * FROM documents
ORDER BY embedding <&> to_bm25query('idx_embedding', query_vec)
LIMIT 10;
```

**주요 함수**:
- `to_bm25query(index_name: text, vector: bm25vector)` -> `bm25query`
- 입력 벡터는 이미 토큰화된 `bm25vector` 필요

---

## 2. pg_tokenizer.rs 아키텍처

### 2.1 핵심 컴포넌트

```
+-----------------------------+
| PostgreSQL 함수 인터페이스   |
+-----------------------------+
| text_analyzer (string tokens)|
|   - pre_tokenizers (정규화) |
|   - token filters (필터링)  |
+-----------------------------+
| model (string -> integer ID) |
|   - pre-trained (BERT, etc) |
|   - huggingface import      |
|   - lindera (일본어/한국어) |
|   - custom (corpus 기반)    |
+-----------------------------+
```

### 2.2 Pre-tokenizers (사용 가능)

| Pre-tokenizer | 언어 | 출력 |
|----------------|------|------|
| `unicode_segmentation` | Unicode 기반 | 공백 기준 단어 분리 |
| `regex` | 정규표현식 | 패턴 기반 분리 |
| `jieba` | 중국어 | 형태소 분석 |
| **(없음)** | **한국어** | **직접 지원 불가** |

**한계**: Pre-tokenizer 수준에서 한국어 형태소 분석 불가. MeCab 같은 별도 분석기 필요.

### 2.3 Token Filters

| Filter | 기능 | 한국어 적용 |
|--------|------|-----------|
| `skip_non_alphanumeric` | 알파벳/숫자만 유지 | 한글 제거 위험 |
| `stemmer` | 단어 정규화 | 영어 중심 |
| `stopwords` | 불용어 제거 | 커스텀 목록 필요 |
| `synonym` | 동의어 확장 | 사전 구성 필요 |
| **`pg_dict`** | **PG 사전 활용** | **제한적 한국어 지원** |

**pg_dict Filter 상세**:
```rust
// pg_tokenizer.rs의 pg_dict 필터
// PostgreSQL dictionary 참조 가능
// 하지만 parser(MeCab) 수준 아님 — dictionary 수준만 지원
```

### 2.4 Models (토큰 -> ID 매핑)

| Model Type | 특징 | 한국어 |
|------------|------|--------|
| **Pre-trained** | BERT, LLMLingua2 | 한글 가능 (모델 의존) |
| **Huggingface** | 외부 모델 import | 한글 모델 사용 가능 |
| **Lindera** | 일본어/한국어 | [O] `lindera-ko-dic` |
| **Custom** | Corpus 기반 빌드 | [O] 최고 유연성 |

#### Lindera Korean 모델 (중요)

```rust
// pg_tokenizer.rs에서 한국어 지원
create_lindera_model(
    kind: "korean",
    dict: "lindera-ko-dic",  // ko-dic 필요
    mode: "normal"            // 또는 "search", "decompose"
)
```

**제약**:
- Feature flag 필수: `lindera-ko-dic` 활성화 필요
- Compile 시간 증가 (사전 포함)
- 품질: NDCG = 0.23 (Phase 5 검증) — textsearch_ko의 0.64 대비 낮음

**결론**: Lindera는 가능하지만 품질 부족.

---

## 3. textsearch_ko 구조 (우리 포크)

### 3.1 핵심 함수

```sql
-- MeCab 형태소 분석 (한국어 최적화)
SELECT to_tsvector('public.korean', text);

-- 토큰 배열 추출
SELECT tsvector_to_array(to_tsvector('public.korean', text));
-- 결과: text[] 배열
```

### 3.2 성능 지표

| 항목 | 값 | 비교 |
|------|-----|------|
| **MIRACL NDCG** | 0.64 | Lindera: 0.23 (2.8배 우수) |
| **Tokenizer** | MeCab | 형태소 레벨 분석 |
| **위치** | extensions/textsearch_ko/ | PostgreSQL 내장 |
| **검증** | Phase 1~5 전체 | 실제 쿼리 테스트 완료 |

---

## 4. 연결 경로 분석 (Path Analysis)

### 4.1 가능성 검토 매트릭스

| 경로 | 방법 | 실현성 | 장점 | 단점 | 영향도 |
|------|------|--------|------|------|--------|
| **A** | pg_tokenizer에서 PG tsconfig 직접 지정 | [X] 불가 | 설정만으로 연결 | Pre-tokenizer에 tsconfig 타입 없음 | - |
| **B** | Python-side textsearch_ko -> bm25vector 구성 | [O] 가능 | 높은 품질 (0.64) | 외부 처리 필요 | 높음 |
| **C** | pg_dict filter로 textsearch_ko 연결 | [~] 부분 | PG 내장 | Dictionary 수준, Parser(MeCab) 아님 | 낮음 |
| **D** | Lindera ko-dic (pg_tokenizer) | [~] 조건부 | 설정만 변경 | 품질 낮음 (0.23), compile feature 필요 | 중간 |

### 4.2 경로별 기술 상세

#### 경로 A: PG tsconfig 직접 지정 (불가)

```rust
// pg_tokenizer.rs의 pre_tokenizer 구조
pub enum PreTokenizer {
    UnicodeSegmentation,
    Regex(String),
    Jieba,
    // [X] PostgreSQL tsconfig 타입 없음
}

// 해결 불가: text_analyzer가 Rust 구조만 지원
```

**이유**: text_analyzer 내부가 Rust 기반이므로 PG의 동적 tsconfig 참조 불가.

---

#### 경로 B: Python-side textsearch_ko -> bm25vector 구성 (가능)

**데이터 흐름**:

```
문서 텍스트
    |
    v
[Python] tsvector_to_array() 호출
    |
    v
토큰 배열: ['부산', '도시', '관광']
    |
    v
[Custom Model] 토큰 -> ID 매핑
    |
    v
ID 배열: [42, 156, 89]
    |
    v
[Python] 토큰 빈도 계산 + 벡터 리터럴 구성
    |
    v
bm25vector: '{42:1, 156:1, 89:1}'::bm25vector
    |
    v
PostgreSQL INSERT
    |
    v
BM25 인덱스 자동 계산 (IDF, posting list)
```

**SQL 예시**:

```sql
-- 1. 토큰 배열 추출 (textsearch_ko 활용)
CREATE TEMP TABLE token_extraction AS
SELECT
    id,
    tsvector_to_array(to_tsvector('public.korean', content)) as tokens
FROM documents;

-- 2. Python에서 처리:
-- tokens = ['부산', '도시', '관광']
-- vocab = CustomModel.load('ko_corpus.model')
-- ids = [vocab[t] for t in tokens]
-- freqs = Counter(ids)  # {42: 1, 156: 1, 89: 1}
-- vector_literal = '{' + ','.join(f'{k}:{v}' for k,v in freqs.items()) + '}'

-- 3. 삽입 및 인덱싱
ALTER TABLE documents ADD COLUMN embedding bm25vector;
UPDATE documents SET embedding = '{42:1, 156:1, 89:1}'::bm25vector WHERE id = 1;
CREATE INDEX idx_bm25 ON documents USING bm25 (embedding bm25_ops);

-- 4. 검색
SELECT id, title FROM documents
ORDER BY embedding <&> to_bm25query('idx_bm25', '{156:1}'::bm25vector)
LIMIT 10;
```

**장점**:
- textsearch_ko의 높은 품질 (NDCG 0.64) 유지
- 완전한 제어 가능 (vocabulary, 전처리 등)
- PostgreSQL 소스 수정 불필요

**단점**:
- 외부 (Python) 처리 필요
- ETL 파이프라인 구축 필요
- 배포 복잡도 증가

---

#### 경로 C: pg_dict filter 활용 (부분 가능)

```rust
// pg_tokenizer.rs의 pg_dict filter
TokenFilter::PgDict {
    dictionary: "korean_stem",
}
```

**제약**:
- Dictionary 수준만 지원 (단어 필터링, 정규화)
- Parser 수준 미지원 (형태소 분석 X)
- MeCab의 형태소 분석 기능 재현 불가
- 결과: 단순 사전 기반 매칭만 가능

**예**: "부산은" -> dictionary lookup -> "부산" 추출 (형태소 분석 아님)

---

#### 경로 D: Lindera ko-dic (조건부 가능)

```rust
// pg_tokenizer.rs에서 compile feature 활성화 필요
#[cfg(feature = "lindera-ko-dic")]
fn create_korean_model() {
    Lindera::new(
        DictionaryKind::Ko,
        Mode::Normal,
    )
}
```

**요구사항**:
1. Cargo.toml에서 feature 활성화:
   ```toml
   [features]
   lindera-ko-dic = ["lindera/ko-dic"]
   ```

2. 재컴파일 (시간 소요):
   ```bash
   cargo build --release --features lindera-ko-dic
   ```

**성능 지표**:
- NDCG: 0.23 (Phase 5)
- textsearch_ko 대비: 2.8배 낮음
- 인덱싱 시간: 더 빠름
- 메모리: lindera 사전 로드 필요

**결론**: 설정은 간단하지만 품질 저하.

---

## 5. 권장 방식: 경로 B (Python-side 통합)

### 5.1 구현 아키텍처

```
+-------------------------------------------+
|   애플리케이션 레이어 (Python)            |
+-------------------------------------------+
|                                           |
| 1. 문서 처리 파이프라인                  |
|    ├─ DB 쿼리: tsvector_to_array()       |
|    ├─ 토큰 배열 획득 (textsearch_ko)    |
|    └─ Token frequency 계산               |
|                                           |
| 2. Vocabulary 관리                       |
|    ├─ Custom model 로드/생성             |
|    |  (Korean corpus 기반)               |
|    └─ Token -> ID 매핑 (bidirectional) |
|                                           |
| 3. BM25Vector 구성                       |
|    ├─ {id: frequency} 딕셔너리 생성     |
|    └─ PostgreSQL 리터럴 형식 변환       |
|       '{id1:freq1, id2:freq2, ...}'    |
|                                           |
+-------------------------------------------+
              |
              v
+-------------------------------------------+
|   PostgreSQL (VectorChord-BM25)          |
+-------------------------------------------+
|                                           |
| embedding column: bm25vector 저장       |
| CREATE INDEX USING bm25 (embedding)     |
| -> IDF 계산 (자동)                      |
| -> Block-WeakAnd posting list 생성      |
|                                           |
+-------------------------------------------+
              |
              v
   검색: ORDER BY embedding <&> query_vec
```

### 5.2 구현 단계

#### Stage 1: Vocabulary 준비

```python
from pg_tokenizer import CustomModel
import json

# 한국어 corpus에서 vocabulary 생성
ko_corpus = [
    "부산은 한국의 주요 항구 도시입니다.",
    "관광객들이 많이 방문하는 곳입니다.",
    # ... 수천 개 문서
]

# Custom model 생성
model = CustomModel.from_corpus(ko_corpus)
model.save('ko_vocab.model')

# Vocabulary 매핑 저장
vocab_map = model.get_vocabulary()
with open('ko_vocab.json', 'w') as f:
    json.dump(vocab_map, f)
```

#### Stage 2: 토큰 배열 추출 (textsearch_ko)

```sql
-- PostgreSQL 쿼리 (textsearch_ko)
SELECT
    id,
    content,
    tsvector_to_array(
        to_tsvector('public.korean', content)
    ) as tokens
FROM documents
WHERE id <= 10000;  -- 배치 처리
```

#### Stage 3: BM25Vector 생성 및 저장

```python
import psycopg2
from collections import Counter
import json

# Vocabulary 로드
with open('ko_vocab.json') as f:
    vocab = json.load(f)

conn = psycopg2.connect("dbname=textsearch_db")
cur = conn.cursor()

# 토큰 배열 -> BM25Vector 변환
query = "SELECT id, tokens FROM token_extraction LIMIT 1000;"
cur.execute(query)

for doc_id, tokens in cur.fetchall():
    # 토큰 -> ID 매핑
    token_ids = [vocab.get(t, 0) for t in tokens if t in vocab]

    # 빈도 계산
    freqs = Counter(token_ids)

    # BM25Vector 리터럴 생성
    vector_parts = [f"{tid}:{freq}" for tid, freq in sorted(freqs.items())]
    vector_literal = '{' + ','.join(vector_parts) + '}'

    # 저장
    cur.execute(
        "UPDATE documents SET embedding = %s::bm25vector WHERE id = %s;",
        (vector_literal, doc_id)
    )

conn.commit()
conn.close()
```

#### Stage 4: 인덱싱

```sql
-- BM25 인덱스 생성 (IDF 자동 계산)
CREATE INDEX idx_documents_bm25 ON documents USING bm25 (embedding bm25_ops);

-- 인덱스 통계 확인
SELECT * FROM pg_indexes WHERE indexname = 'idx_documents_bm25';
```

#### Stage 5: 검색

```sql
-- 쿼리 토큰화 (동일 vocabulary 사용)
-- Python에서: query_tokens = ['부산', '도시']
--            query_ids = [vocab[t] for t in query_tokens]
--            query_vec = '{42:1, 156:1}'::bm25vector

SELECT
    id,
    title,
    similarity
FROM documents
ORDER BY embedding <&> '{42:1, 156:1}'::bm25vector
LIMIT 10;
```

### 5.3 핵심 SQL 함수 요약

| 함수 | 목적 | 입력 | 출력 |
|------|------|------|------|
| `tsvector_to_array(tsvector)` | 텍스트 벡터 -> 토큰 배열 | tsvector | text[] |
| `to_tsvector(config, text)` | 텍스트 -> tsvector | text config, text | tsvector |
| `bm25vector` (타입) | BM25 희소벡터 저장 | '{id:freq}' 리터럴 | bm25vector |
| `to_bm25query(idx, vec)` | 벡터 -> 쿼리 변환 | index name, bm25vector | bm25query |
| `<&>` (연산자) | BM25 유사도 스코어 | bm25vector 양쪽 | float8 (음수) |

---

## 6. VectorChord-BM25 vs pl/pgsql v2 비교

### 6.1 인덱스 구조 비교

| 항목 | pl/pgsql v2 | VectorChord-BM25 |
|------|------------|------------------|
| **기초** | B-tree inverted index | Block-WeakAnd posting list |
| **IDF 계산** | 쿼리 시간, stats 테이블 전체 스캔 | 쿼리 시간, 인덱스 메타데이터 |
| **I/O 패턴** | 랜덤 액세스 (B-tree 탐색) | 순차 I/O (posting list) |
| **메모리** | RAM 제한 (inverted_index 크기) | 디스크 기반, 효율적 압축 |
| **쿼리 성능** | 문서 많을수록 저하 | 규모 독립적 |

### 6.2 성능 특성

```
pl/pgsql v2:
+-----------+
| Stats Table 스캔 | <- 모든 쿼리에서 실행
+-----------+
| Inverted Index |
| (B-tree)      |
+-----------+
| RAM 제약        | <- 대규모 데이터셋 병목
+-----------+

VectorChord-BM25:
+-----------+
| Index Metadata | <- 빠른 IDF 조회
+-----------+
| Block-WeakAnd   |
| Posting Lists   |
+-----------+
| 순차 I/O        | <- SSD 최적화
+-----------+
```

### 6.3 선택 기준

| 시나리오 | 추천 | 이유 |
|---------|------|------|
| **<100K 문서, 낮은 QPS** | pl/pgsql v2 | 구현 단순, 저장소 최소 |
| **>1M 문서, 높은 QPS** | VectorChord-BM25 | 순차 I/O, 규모 확장성 |
| **실시간 색인 업데이트** | pl/pgsql v2 | 트리거 기반 즉시 반영 |
| **배치 색인 + 쿼리 최적화** | VectorChord-BM25 | 전용 인덱싱 엔진 |

---

## 7. 구현 체크리스트

### 경로 B 구현 (권장)

- [ ] **Preparation**
  - [ ] Korean corpus 수집 (최소 10K 문서)
  - [ ] Custom model vocabulary 생성
  - [ ] Vocabulary 파일 저장 (JSON)

- [ ] **Database Setup**
  - [ ] textsearch_ko 설치 (MeCab 기반)
  - [ ] embedding column 추가 (bm25vector 타입)
  - [ ] 토큰 추출 쿼리 검증

- [ ] **ETL Pipeline**
  - [ ] Python 배치 스크립트 작성
  - [ ] 토큰 -> ID 매핑 구현
  - [ ] BM25Vector 리터럴 생성
  - [ ] 배치 INSERT 최적화

- [ ] **Indexing**
  - [ ] BM25 인덱스 생성
  - [ ] 인덱스 통계 확인
  - [ ] 메모리 사용량 모니터링

- [ ] **Verification**
  - [ ] 샘플 쿼리 검증 (상위 10개 결과)
  - [ ] NDCG 측정 (MIRACL 평가셋)
  - [ ] 쿼리 응답시간 측정

### 경로 D 구현 (대안)

- [ ] pg_tokenizer Cargo.toml 수정
- [ ] lindera-ko-dic feature 활성화
- [ ] 재컴파일 (시간 소요)
- [ ] 품질 검증 (기대값: NDCG ~0.23)

---

## 8. 결론

### 최적 선택: 경로 B (Python-side textsearch_ko 통합)

**이유**:
1. **품질**: NDCG 0.64 (Lindera 0.23 대비 2.8배)
2. **유연성**: 외부 vocabulary 관리, 전처리 커스터마이징
3. **독립성**: PostgreSQL 소스 수정 불필요
4. **검증됨**: Phase 1~5 실제 쿼리 테스트 완료

**트레이드오프**:
- ETL 파이프라인 구축 필요 (1~2주)
- 배치 처리 방식 (실시간 업데이트 제약)
- Python 의존성 추가

### 대안: 경로 D (Lindera ko-dic)

**장점**:
- PostgreSQL 내장, 설정 간단
- 동적 색인 업데이트 가능

**단점**:
- 품질 낮음 (NDCG 0.23)
- 컴파일 시간 증가
- Feature flag 관리 필요

---

## 9. 참고

### VectorChord-BM25 공식 문서
- GitHub: tensorchord/VectorChord-bm25
- 버전: v0.3.0
- 라이선스: Apache 2.0

### pg_tokenizer.rs 공식 문서
- GitHub: tensorchord/pg_tokenizer.rs
- 버전: v0.1.1
- Lindera 통합: `lindera-ko-dic` feature flag

### textsearch_ko (우리 포크)
- 위치: extensions/textsearch_ko/
- 기반: PostgreSQL contrib 텍스트 검색
- MeCab 의존성: libmecab, mecab-ko-dic

---

**작성**: Writer Agent | **검증**: Phase 6 분석 | **최종 업데이트**: 2026-03-25
