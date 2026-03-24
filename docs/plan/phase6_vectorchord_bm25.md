# Phase 6: VectorChord-BM25 + pg_tokenizer — 한국어 BM25

## 목표

VectorChord-BM25(Block-WeakAnd) + pg_tokenizer.rs 조합으로 한국어 BM25 검색을 구현한다.
pg_tokenizer에 MeCab/Kiwi 토크나이저를 연결하여, PG 안에서 Lucene급 BM25 + 한국어 형태소 분석을 달성.

성공 시: **직접 포크(Phase 7/8) 없이** 기성 확장 조합만으로 한국어 BM25 해결.

## 의존성

- Phase 5 완료 (한국어 BM25 문제 정량화 완료)
- Docker 환경 (tensorchord/vchord-suite 이미지)

---

## 배경

### Phase 5에서 확인된 PG BM25 한국어 문제

| 확장 | 문제 |
|------|------|
| pg_textsearch (Timescale) | `<@>` 연산자 AND 매칭, OR 불가 |
| pg_search (ParadeDB) | korean_lindera = 일본어 사전 기반, NDCG=0.23 |
| pl/pgsql BM25 v2 | B-tree 테이블 → 1M+ 스케일링 한계 |

### VectorChord-BM25가 해결할 수 있는 것

| 항목 | VectorChord-BM25 |
|------|-----------------|
| 인덱스 구조 | **Block-WeakAnd** (posting list + WAND) — Lucene급 I/O |
| 토크나이저 | **pg_tokenizer.rs** — 분리 설계, 커스텀 연결 가능 |
| 스케일링 | posting list 구조로 대규모 유리 |
| PG 통합 | USING bm25 인덱스, SQL 네이티브 |

---

## 핵심 확인 사항 (Phase 6-0: 타당성 조사)

Phase 6 실행 전 반드시 확인:

### 1. pg_tokenizer 한국어 토크나이저 연결 가능성

```
Q: pg_tokenizer.rs에 MeCab 또는 Kiwi를 연결할 수 있는가?
확인 대상:
  - docs/06-model.md (토크나이저 모델 목록)
  - docs/04-usage.md (커스텀 토크나이저 사용법)
  - 소스 코드: tokenizer trait / plugin 인터페이스
```

가능한 시나리오:
- **Best**: pg_tokenizer가 외부 토크나이저 플러그인을 지원 → MeCab FFI 연결
- **Good**: lindera에 mecab-ko-dic 추가 가능 → 사전만 교체
- **OK**: bert/llmlingua2 등 subword tokenizer로 한국어 토크나이징 → BM25 품질 확인 필요
- **Bad**: 커스텀 토크나이저 불가 → pg_tokenizer 포크 필요 (Phase 7/8과 비슷한 비용)

### 2. Block-WeakAnd의 OR 매칭 지원

```
Q: pg_textsearch처럼 AND 매칭만 하는가, OR 매칭도 되는가?
확인 대상:
  - bm25_ops 연산자의 쿼리 파싱 로직
  - to_bm25query() 함수의 동작
```

### 3. bert tokenizer의 BM25 적합성

```
Q: subword tokenizer(bert)로 BM25를 돌리면 품질이 나오는가?
리스크: "검색" → ["검", "##색"] 같은 subword 분할은 BM25 IDF에 부적합
확인: MIRACL에서 bert tokenizer 기반 BM25 NDCG 측정
```

---

## 실험 계획

### Phase 6-0: 타당성 조사 (1일)

1. vchord-suite Docker 실행
2. pg_tokenizer 문서/소스 분석 — 커스텀 토크나이저 연결 방법 확인
3. 기본 bert tokenizer로 한국어 BM25 NDCG 측정 (baseline)
4. 한국어 토크나이저 연결 가능 여부 판정

**Go/No-Go 판정:**
- Go: MeCab/Kiwi 연결 가능 → Phase 6-1~6-3 진행
- Partial Go: bert만 가능 → NDCG 측정 후 판단
- No-Go: 커스텀 불가 + bert NDCG 불합격 → Phase 7(pg_textsearch 포크)로 진행

### Phase 6-1: 한국어 토크나이저 연결 (2일)

pg_tokenizer에 MeCab(또는 Kiwi) 연결:
- 옵션 A: pg_tokenizer의 플러그인 인터페이스 사용 (있다면)
- 옵션 B: pg_tokenizer.rs에 MeCab FFI 토크나이저 추가 (소스 수정)
- 옵션 C: lindera에 mecab-ko-dic 사전 추가

### Phase 6-2: 벤치마크 (1일)

Phase 5와 동일 조건:
- MIRACL + EZIS NDCG@10, R@10
- latency p50/p95
- QPS@1/4/8 concurrent
- 비교 대상: pl/pgsql v2, pg_textsearch AND, pg_search korean_lindera

### Phase 6-3: 스케일링 테스트 (1일)

Phase 5에서 확인한 pl/pgsql v2의 B-tree 한계를 VectorChord-BM25가 해결하는지:
- 1k / 10k / 100k docs 규모별 latency
- EXPLAIN ANALYZE — posting list vs B-tree I/O 패턴 확인
- 메모리 사용량 비교

---

## 핵심 가설

| 항목 | 예상 |
|------|------|
| MeCab 연결 후 MIRACL NDCG | 0.60~0.65 (pl/pgsql v2 0.34 대비 향상 — OR 매칭 효과) |
| bert tokenizer MIRACL NDCG | 0.25~0.35 (subword 분할로 BM25 부적합 예상) |
| latency | sub-ms ~ 수 ms (Block-WAND) |
| 스케일링 | posting list 구조로 10M+ 가능 (B-tree 대비 우위) |

성공 기준: **한국어 토크나이저 연결 성공 & MIRACL NDCG ≥ 0.55 & latency < 5ms**

---

## Phase 7/8과의 관계

| | Phase 6 (VectorChord-BM25) | Phase 7 (pg_textsearch 포크) | Phase 8 (pg_search 포크) |
|---|---|---|---|
| 접근 | 기성 확장 조합 | Rust 소스 수정 | Rust 소스 수정 + FFI |
| 난이도 | **낮음** (설치+설정) | 중간 | 높음 |
| upstream 호환 | **유지** (플러그인) | 포크로 상실 | 포크로 상실 |
| 실패 시 | Phase 7/8로 fallback | Phase 8로 fallback | pl/pgsql v2 유지 |

Phase 6이 성공하면 Phase 7/8은 **불필요**. 실패 시에만 포크 경로 진행.

---

## 출력

- `experiments/phase6_vectorchord/phase6_bench.py` — 벤치마크 스크립트
- `results/phase6/phase6_vectorchord_bm25.json` — 실험 결과
- `results/phase6/phase6_report.md` — pg_tokenizer 분석 + 벤치마크 리포트
