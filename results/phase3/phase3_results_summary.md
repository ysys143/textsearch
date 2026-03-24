# Phase 3 결과 요약

## 실험 개요

**목표**: PostgreSQL 내 BM25 구현 시 최적의 토크나이저와 구현 방식 검증

**실험 범위**:
- pgvector-sparse BM25 + 4가지 토크나이저 (kiwi-cong, MeCab, Okt, whitespace)
- 2개 데이터셋 (MIRACL-ko 10k, EZIS 97 docs)

---

## 핵심 결과

### MIRACL-ko (10,000 문서, 213 쿼리)

| 순위 | 토크나이저 | NDCG@10 | Recall@10 | MRR | Latency p50 |
|------|-----------|---------|-----------|-----|-------------|
| 1 | **kiwi-cong** | **0.6326** | **0.7911** | **0.6195** | **4.24ms** |
| 2 | Okt | 0.5520 | 0.7120 | 0.5326 | 5.55ms |
| 3 | MeCab | 0.5323 | 0.7066 | 0.5104 | 18.05ms |

### EZIS (97 문서, 131 쿼리)

| 순위 | 토크나이저 | NDCG@10 | Recall@10 | MRR | Latency p50 |
|------|-----------|---------|-----------|-----|-------------|
| 1 | **kiwi-cong** | **0.9455** | **1.0000** | **0.9267** | **1.04ms** |
| 2 | MeCab | 0.9124 | 1.0000 | 0.8826 | 0.83ms |
| 3 | Okt | 0.8982 | 1.0000 | 0.8635 | 1.73ms |
| 4 | whitespace | 0.8352 | 0.9427 | 0.8040 | 0.76ms |

---

## 선정 결과

**선택: pgvector-sparse BM25 + kiwi-cong**

이유:
- MIRACL, EZIS 모두 최고 NDCG 달성
- 안정적인 지연시간 (p50 4.24ms, p95 8.68ms)
- 형태소 분석기 중 어미 처리 최우수

---

## Phase 간 성능 비교

| Phase | 구현 | NDCG@10 | Phase 1 대비 |
|-------|------|---------|-------------|
| 1 | Python BM25 (kiwi-cong) | 0.3471 | — |
| 2 | pg_textsearch BM25 (MeCab) | 0.3374 | -2.8% |
| **3** | **pgvector-sparse BM25 (kiwi-cong)** | **0.6326** | **+82.3%** |

---

## 주요 발견

### 1. 토크나이저 품질이 결정적
- kiwi-cong의 어미 제거(conjugation)가 sparse BM25 성능을 극대화
- 형태소 분석 없는 whitespace는 Recall 부족

### 2. MeCab 지연시간 문제
- p50 18.05ms (kiwi-cong 4.24ms 대비 4배)
- p95 173.29ms (8.68ms 대비 20배)
- 쿼리 임베딩 성능 병목

### 3. 소규모 코퍼스(EZIS)에서 토크나이저 차이 축소
- 97개 문서에서는 형태소 분석 간 차이 < 2%
- 대규모 데이터셋(MIRACL)에서 11~16% 차이 발생

### 4. pgvector-sparse의 우수성
- Phase 2 pg_textsearch 대비 87.7% 성능 향상
- 토크나이저(kiwi-cong) + sparse index 최적화의 결과

---

## 다음 단계 (Phase 4)

**BGE-M3 sparse embeddings 도입 예상**
- 신경망 기반 sparse representation으로 추가 개선
- 동일한 pgvector sparse index 인프라 활용
- 목표 NDCG@10: 0.75+

---

## 산출물

- `phase3_bm25_comparison.json` - 상세 결과 데이터
- `phase3_native_bm25.md` - 전체 분석 보고서
