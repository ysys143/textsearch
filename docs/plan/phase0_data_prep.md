# Phase 0: 데이터 준비

## 목표

모든 phase에서 공통으로 사용할 두 종류의 데이터셋을 준비한다.

---

## 0-A. MIRACL-ko

**상태**: 완료

- 213 dev queries, 10,000 Wikipedia passages
- 저장 위치: `data/docs_ko_miracl.json`, `data/queries_dev.json`
- 포맷: `[{id, text}]` / `[{query_id, text, relevant_ids}]`

---

## 0-B. EZIS Oracle Manual QA Set

**목적**: 도메인 특화 기술 문서(DB 모니터링 솔루션 매뉴얼)로 BM25 유리 시나리오 검증

### 원본 문서

- `data/EZIS_Oracle_Manual.pdf` — 109 pages, 한국어 기술 매뉴얼
- 내용: DBMS 성능 모니터링 솔루션 사용자 가이드 (Setting, Activity, Performance, Wait Analysis, SQL Analysis, Event Analysis 등)

### 파이프라인

```
1. PDF 파싱          pdfplumber로 페이지별 텍스트 추출
2. 섹션 경계 감지    목차 기반 (1장~13장, 소제목 단위)
3. 청킹              섹션 내 슬라이딩 윈도우 (500~2000자, overlap 100자)
4. 질문 생성         Claude API로 청크당 2~3개 hard question 생성
5. 필터링            trivial/중복/답 불가 질문 제거
6. 레이블링          각 질문의 relevant chunk id 기록
```

### 청킹 전략

- 섹션 제목(2.1, 2.2, ... 형식)을 청크 경계로 우선 사용
- 표는 직전 설명 텍스트와 합산
- 이미지 캡션은 포함, 이미지 자체는 제외
- 최소 200자 미만 청크는 다음 청크와 병합
- 예상 청크 수: 180~220개

### 질문 생성 전략

**Hard question 유형:**

| 유형 | 예시 질문 |
|------|-----------|
| 절차형 | "SSO 서버 장애 시에도 EZIS 로그인을 유지하려면 어떻게 설정하는가?" |
| 비교형 | "Server Group과 Cloud Group 등록 방식의 차이점은 무엇인가?" |
| 조건형 | "RAC 환경에서 서버를 모니터링할 때 일반 서버 등록과 다른 설정 항목은?" |
| 다중참조형 | "AWR Report를 자동 생성하기 위해 필요한 설정 단계를 순서대로 설명하라" |
| 부정형 | "Stat Name 설정에서 수집하지 않도록 제외할 수 있는 항목은?" |

**생성 프롬프트 방향:**
- 해당 청크를 읽지 않으면 답하기 어려운 질문
- 단순 키워드 검색으로 답하기 어려운 질문 (우회적 표현, 문맥 필요)
- 정답이 명확하게 해당 청크에 있는 질문 (레이블링 가능)

### 출력 파일

```
data/ezis_chunks.json          # [{id, text, section, page_start, page_end}]
data/queries_ezis.json         # [{query_id, text, relevant_ids}]  — MIRACL 동일 포맷
```

### 예상 규모

| 항목 | 수량 |
|------|------|
| 총 청크 수 | ~200 |
| 생성 질문 수 (raw) | ~500 |
| 필터링 후 최종 queries | ~120 |

### 구현 위치

`benchmark/data/load_ezis.py` — PDF 파싱, 청킹, QA 생성 파이프라인
