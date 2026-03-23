# Phase 3: pgvector-sparse BM25 — Tokenizer 비교

## 목표

pgvector-sparse 벡터를 활용한 BM25 검색에서 **tokenizer 선택이 검색 품질에 얼마나 영향을 미치는가** 측정.

Phase 1에서 Python-side BM25로 비교했던 tokenizer들을 이번엔 **DB 내 sparse index** 기반으로 재평가.

> **집행 범위 노트**: 원래 계획(Phase 3-A pg_bm25, 3-B pl/pgsql BM25, 3-C tsvector)은
> Phase 2 실행 시 함께 처리됨 (→ `phase2_tsvector_korean.md` 참조).
> Phase 3는 pgvector-sparse BM25 tokenizer 비교 실험으로 재정의됨.

## 의존성

- Phase 0 완료 (pgvector, sparsevec 설치)
- Phase 1 완료 (tokenizer 후보: kiwi-cong, mecab, okt, whitespace)
- Phase 2 완료 (BM25가 ts_rank_cd보다 우수함 확인)

---

## 방법

**공통 구조**: `BM25Embedder_PG`로 각 tokenizer마다 sparse vector를 계산하여 pgvector `sparsevec` 컬럼에 저장 후 내적(`<#>`) 연산으로 검색.

```python
# 인덱싱
emb = BM25Embedder_PG(tokenizer='kiwi-cong')
emb.fit(corpus_texts)
sparse_vec = emb.embed_document(text)  # sparsevec

# 검색
SELECT id FROM text_embedding_sparse_bm25_kiwi_cong
ORDER BY emb_sparse <#> query_vec LIMIT 10
```

MIRACL은 10k corpus에 pre-built sparse index 사용 (빌드 시간 절약).
EZIS는 97 docs로 소규모이므로 in-memory BM25로 평가.

---

## 실험 대상 및 결과

### MIRACL-ko (10k corpus, 213 queries)

| 방법 | Tokenizer | NDCG@10 | R@10 | MRR | Latency p50 |
|------|---------|---------|------|-----|-------------|
| 3-kiwi | kiwi-cong (형태소) | **0.6326** | 0.7911 | 0.6195 | 4.24ms |
| 3-okt  | Okt (형태소) | 0.5520 | 0.7120 | 0.5326 | 5.55ms |
| 3-mecab | MeCab (형태소) | 0.5323 | 0.7066 | 0.5104 | 18.05ms |

### EZIS (97 docs, 131 queries)

| 방법 | Tokenizer | NDCG@10 | R@10 | MRR | Latency p50 |
|------|---------|---------|------|-----|-------------|
| 3-ezis-kiwi | kiwi-cong | **0.9455** | 1.0000 | 0.9267 | 1.04ms |
| 3-ezis-mecab | MeCab | 0.9124 | 1.0000 | 0.8826 | 0.83ms |
| 3-ezis-okt | Okt | 0.8982 | 1.0000 | 0.8635 | 1.73ms |
| 3-ezis-ws | whitespace | 0.8352 | 0.9427 | 0.8040 | 0.76ms |

---

## Phase 간 비교 (MIRACL, kiwi-cong 기준)

| Phase | 방법 | NDCG@10 | 비고 |
|-------|------|---------|------|
| Phase 1 | Python BM25 kiwi-cong | 0.3471 | Python-side 계산 |
| Phase 2 | pg_textsearch + MeCab BM25 | 0.3374 | DB 내부 BM25, MeCab |
| **Phase 3** | **pgvector-sparse BM25 kiwi-cong** | **0.6326** | DB sparse index |
| Phase 4 | BGE-M3 sparse (neural) | 0.7634 | 신경망 sparse |

pgvector-sparse Phase 3: Phase 1 Python BM25 대비 **+82.3% NDCG**.
kiwi-cong의 우수한 형태소 분석이 sparse BM25와 결합했을 때 극대화됨.

---

## 핵심 발견

### 1. kiwi-cong이 일관적으로 최우수
MIRACL, EZIS 모두 kiwi-cong 1위. Phase 1 결과와 일치.
conjugation 처리(어미 제거)가 sparse BM25 recall에 결정적.

### 2. pgvector-sparse BM25가 pg_textsearch BM25보다 대폭 우수 (MIRACL)
Phase 2 pg_textsearch + MeCab BM25: 0.3374
Phase 3 pgvector-sparse + kiwi-cong BM25: 0.6326 (+87%)
원인: tokenizer 품질(kiwi-cong > MeCab) + Python-side BM25 파라미터 튜닝 가능성

### 3. MeCab의 latency 열위
MeCab p50=18.05ms vs kiwi-cong 4.24ms — 쿼리 임베딩 시간이 latency를 지배.

### 4. EZIS는 tokenizer 차이가 MIRACL보다 작음
97개 소규모 코퍼스에서는 모든 형태소 분석기가 NDCG 0.89 이상, R@10=1.0.
충분한 recall을 위해선 tokenizer 품질보다 BM25 자체가 더 중요.

---

## 출력

- `results/phase3/phase3_bm25_comparison.json`
- `experiments/phase3_native_bm25/phase3_bm25_comparison.py`
