# Phase 8: pg_search (ParadeDB) 포크 — 한국어 지원 해결

## 목표

ParadeDB pg_search를 포크하여 MeCab 또는 Kiwi 토크나이저를 Tantivy에 연결한다.
성공 시: Tantivy의 posting list + WAND 성능 + 한국어 형태소 분석 = PG 내장 ES급 BM25.

## 의존성

- Phase 5 완료 (korean_lindera 한계 정량화 완료)
- Rust + pgrx + Tantivy 빌드 환경

---

## 배경: Phase 5에서 확인된 문제

| 항목 | 값 |
|------|-----|
| Phase 2 MIRACL NDCG@10 (korean_lindera) | 0.2275 |
| 원인 | Lindera = 일본어 IPAdic 기반, 한국어 형태소 커버리지 부족 |
| 커스텀 토크나이저 연결 | 불가 — Rust pgrx 빌드 타임에 결정, 런타임 교체 불가 |

핵심: **Tantivy의 검색 엔진 아키텍처(posting list + WAND)는 우수하지만, 토크나이저가 한국어를 지원하지 못함.**

## 수정 전략

### 1단계: 소스 분석

- ParadeDB pg_search 리포지토리 클론
- Tantivy tokenizer 인터페이스 분석 (`tantivy::tokenizer::Tokenizer` trait)
- `korean_lindera` 등록 경로 추적
- 커스텀 tokenizer 등록 메커니즘 확인

### 2단계: 한국어 토크나이저 연결

**옵션 A: MeCab FFI (C 바인딩)**
```
Rust → FFI → libmecab.so → mecab-ko-dic
```
- 장점: Phase 1~5에서 검증된 MeCab 품질 그대로 사용
- 장점: textsearch_ko의 MeCab 사전 재사용 가능
- 단점: libmecab 시스템 의존성, Docker 빌드 시 포함 필요

**옵션 B: Kiwi FFI (C++ 바인딩)**
```
Rust → FFI → libkiwi → 내장 사전
```
- 장점: 사전이 바이너리에 내장 — 외부 의존성 없음
- 장점: Phase 1에서 MeCab과 동등한 품질 확인
- 단점: C++ FFI가 C FFI보다 복잡

**옵션 C: Lindera에 mecab-ko-dic 추가**
```
Lindera 포크 → mecab-ko-dic 사전 빌드 → Tantivy에 등록
```
- 장점: Lindera 인터페이스 유지, 침습도 최소
- 단점: Lindera의 한국어 분석 로직 자체가 부족할 수 있음 (사전만 바꿔서 해결될지 불확실)

**권장: 옵션 A (MeCab FFI)** — 가장 검증된 경로.

### 3단계: Tantivy Tokenizer 구현

```rust
// 예시 구조
pub struct MeCabKoreanTokenizer {
    mecab: *mut mecab_t,  // FFI handle
}

impl Tokenizer for MeCabKoreanTokenizer {
    type TokenStream<'a> = MeCabTokenStream<'a>;

    fn token_stream<'a>(&'a mut self, text: &'a str) -> Self::TokenStream<'a> {
        // mecab_sparse_tostr(self.mecab, text)
        // parse output → Token stream
    }
}
```

pg_search의 tokenizer 등록 시점에 `MeCabKoreanTokenizer`를 추가 등록.

### 4단계: 빌드 및 테스트

- Docker 기반 빌드: PostgreSQL + pg_search + libmecab + mecab-ko-dic
- 단위 테스트: MeCab tokenizer 출력 검증 (Phase 1 결과와 동일한 토큰 확인)
- 통합 테스트: `paradedb.search()` + `korean_mecab` tokenizer로 인덱스 생성 및 검색

### 5단계: 벤치마크

Phase 5와 동일 조건에서 측정:
- MIRACL + EZIS NDCG@10, R@10
- latency p50/p95
- QPS@1/4/8 concurrent
- pl/pgsql v2, pg_textsearch 포크(Phase 6) 대비 비교

## 핵심 가설

| 항목 | 예상 |
|------|------|
| MeCab 연결 후 MIRACL NDCG | 0.60~0.65 (korean_lindera 0.23 대비 대폭 향상) |
| latency | sub-ms ~ 수 ms (Tantivy posting list + WAND) |
| QPS | pg_textsearch와 동등 이상 |
| 스케일링 | posting list 구조로 B-tree 대비 대규모에서 유리 |

성공 기준: **MIRACL NDCG ≥ 0.55 & latency < 5ms**

성공 시 의미: PG 안에서 Lucene급 I/O 패턴(posting list + WAND) + 한국어 형태소 분석 → **ES 전환 없이 대규모 스케일링 가능**. Phase 5 보고서의 "PG scale-up 한계 = 메모리에 inverted_index 올리기" 문제를 근본적으로 해결.

## 리스크

| 리스크 | 대응 |
|--------|------|
| MeCab FFI 안정성 | thread-safety 확인, 필요 시 per-thread instance |
| Tantivy tokenizer trait 변경 | pg_search의 Tantivy 버전 고정, API 호환성 확인 |
| 빌드 복잡도 (Rust + C + PostgreSQL) | 멀티스테이지 Docker 빌드로 격리 |
| upstream pg_search 업데이트와 충돌 | 토크나이저 모듈만 분리하여 침습도 최소화 |
| libmecab 시스템 의존성 | Docker에 포함, 또는 옵션 B (Kiwi)로 전환 |

## Phase 6 vs Phase 7 비교

| | Phase 6 (pg_textsearch 포크) | Phase 7 (pg_search 포크) |
|---|---|---|
| 수정 대상 | 쿼리 매칭 로직 (AND→OR) | 토크나이저 (Lindera→MeCab) |
| 수정 난이도 | 낮음~중간 (tsquery 생성 경로) | 중간~높음 (FFI + Tokenizer trait) |
| 스케일링 구조 | WAND (이미 있음) | posting list + WAND (이미 있음) |
| 한국어 해결 경로 | OR 매칭으로 recall 회복 | 형태소 분석 품질 자체를 개선 |
| 대규모 이점 | pg_textsearch 내부 인덱스 사용 | Tantivy posting list → ES급 I/O 패턴 |

둘 다 성공하면, Phase 8 시스템 비교에서 ES/Qdrant와 동등 조건으로 비교 가능.

## 출력

- `forks/pg_search/` — 수정된 소스 (또는 별도 repo)
- `experiments/phase7_pgsearch_fork/phase7_bench.py` — 벤치마크 스크립트
- `results/phase7/phase7_pgsearch_fork.json` — 실험 결과
- `results/phase7/phase7_report.md` — 수정 내용 + 벤치마크 리포트
