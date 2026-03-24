# Phase 6: pg_textsearch 포크 — 한국어 지원 해결

## 목표

pg_textsearch(Timescale)를 포크하여 `<@>` 연산자의 AND 매칭을 OR 매칭으로 수정한다.
성공 시: sub-ms BM25 latency + WAND 최적화 + 한국어 OR 매칭 = PostgreSQL 내장 최적 BM25.

## 의존성

- Phase 5 완료 (한국어 AND 매칭 문제 정량화 완료)
- Rust + pgrx 빌드 환경

---

## 배경: Phase 5에서 확인된 문제

| 현상 | 원인 |
|------|------|
| MIRACL NDCG=0.3437, R@10=0.3915 | `<@>` 연산자가 내부적으로 `plainto_tsquery` (AND) 사용 |
| OR tsquery 외부 주입 불가 | `<@>` 가 text + query string만 받고 자체 파이프라인 실행 |
| `ts_rank_cd` OR fallback 열화 | NDCG=0.2300 — BM25가 아닌 coordinate scoring |

핵심: **BM25 랭킹(`<@>`)과 OR 매칭(`@@`)을 결합할 수 없는 구조적 문제.**

## 수정 전략

### 1단계: 소스 분석

- pg_textsearch 리포지토리 클론 (Timescale GitHub)
- `<@>` 연산자의 쿼리 파싱 경로 추적
- `plainto_tsquery` 호출 위치 특정
- 쿼리 모드 파라미터화 가능 여부 확인

### 2단계: AND→OR 수정

**최소 침습 수정 방향:**

옵션 A: `plainto_tsquery` → `to_tsquery` (OR 구분자) 교체
```
-- 현재: plainto_tsquery('public.korean', '검색 엔진 성능')
--   → '검색' & '엔진' & '성능'  (AND)
-- 수정: to_tsquery('public.korean', '검색 | 엔진 | 성능')
--   → '검색' | '엔진' | '성능'  (OR)
```

옵션 B: GUC 파라미터 추가 (`textsearch.query_mode = 'or'`)
- 기존 AND 동작을 기본으로 유지
- 설정으로 OR 전환 가능

옵션 C: 연산자 오버로드 (`<|>` 등 OR 전용 연산자 추가)
- 기존 `<@>` (AND) 유지 + 새 연산자 추가

### 3단계: 빌드 및 테스트

- pgrx 빌드 환경 구성 (Rust toolchain + PostgreSQL dev headers)
- Docker 기반 빌드 (재현 가능한 환경)
- 단위 테스트: OR tsquery 생성 확인
- 통합 테스트: 한국어 쿼리 결과 검증

### 4단계: 벤치마크

Phase 5와 동일 조건에서 측정:
- MIRACL + EZIS NDCG@10, R@10
- latency p50/p95 (sub-ms 유지 확인)
- QPS@1/4/8 concurrent
- pl/pgsql v2 대비 비교

## 핵심 가설

| 항목 | 예상 |
|------|------|
| OR 수정 후 MIRACL NDCG | 0.55~0.65 (AND 0.34 대비 대폭 향상) |
| latency | sub-ms 유지 (WAND 알고리즘 동일) |
| QPS | 현재 수준 유지 또는 향상 |

성공 기준: **MIRACL NDCG ≥ 0.55 & latency < 2ms**

## 리스크

| 리스크 | 대응 |
|--------|------|
| Rust/pgrx 빌드 환경 복잡 | Docker 기반 reproducible build |
| `<@>` 내부 구조가 깊게 얽혀있을 수 있음 | 소스 분석 후 침습도 판단, 불가 시 옵션 C |
| WAND 알고리즘이 AND 전제로 최적화되었을 수 있음 | OR 모드에서 WAND 성능 저하 시 Top-K 전략 조정 |
| upstream 호환성 상실 | 포크로 관리, upstream 변경분 주기적 머지 |

## 출력

- `forks/pg_textsearch/` — 수정된 소스 (또는 별도 repo)
- `experiments/phase6_textsearch_fork/phase6_bench.py` — 벤치마크 스크립트
- `results/phase6/phase6_textsearch_fork.json` — 실험 결과
- `results/phase6/phase6_report.md` — 수정 내용 + 벤치마크 리포트
