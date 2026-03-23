# Phase 4: BM25 vs Neural Sparse + Hybrid — Full Combination Matrix

## 목표

Phase 3에서 찾은 PostgreSQL 최선 BM25 세팅(kiwi-cong)을 BGE-M3 sparse/dense 모델과 두 가지 융합 전략(RRF, Bayesian)으로 체계적으로 비교.

BM25 × {sparse, dense} × {RRF, Bayesian} 전체 조합 + 단독 방법 3종.

## 의존성

- Phase 0 완료
- Phase 1 완료 (best tokenizer: kiwi-cong)
- Phase 3 완료 (best native BM25: pgvector-sparse + kiwi-cong, NDCG@10=0.6326)

---

## 실험 대상 및 결과

### MIRACL-ko (10k corpus, 213 queries)

| method_id | 방법 | NDCG@10 | R@10 | MRR | Latency p50 |
|-----------|------|---------|------|-----|-------------|
| 4-bgem3-dense | BGE-M3 dense (cosine, numpy) | **0.7915** | 0.9154 | 0.8013 | 253.0ms |
| 4-bgem3-sparse | BGE-M3 sparse (neural) | 0.7634 | 0.8680 | 0.7830 | 156.9ms |
| 4-hybrid-rrf-dense | Hybrid BM25+BGE-M3 dense (RRF k=60) | 0.7527 | 0.8907 | 0.7487 | 641.0ms |
| 4-bayes-sparse | Bayesian BM25+BGE-M3 sparse | 0.7485 | 0.8821 | 0.7492 | 290.9ms |
| 4-bayes-dense | Bayesian BM25+BGE-M3 dense | 0.7476 | 0.8854 | 0.7442 | 379.3ms |
| 4-hybrid-rrf | Hybrid BM25+BGE-M3 sparse (RRF k=60) | 0.7160 | 0.8694 | 0.7060 | 119.1ms |
| 4-splade-ko | splade-ko (yjoonjang/splade-ko-v1) | 0.6962 | 0.8101 | 0.7174 | 104.67ms |
| 4-bm25-kiwi | BM25 kiwi-cong (pgvector) | 0.6326 | 0.7911 | 0.6195 | 5.7ms |

### EZIS (97 docs, 131 queries)

| method_id | 방법 | NDCG@10 | R@10 | MRR | Latency p50 |
|-----------|------|---------|------|-----|-------------|
| 4-ezis-bayes-dense | Bayesian BM25+BGE-M3 dense | **0.9493** | 1.0000 | 0.9313 | 418.6ms |
| 4-ezis-bm25 | BM25 kiwi-cong (in-memory) | 0.9455 | 1.0000 | 0.9267 | 1.0ms |
| 4-ezis-bayes-sparse | Bayesian BM25+BGE-M3 sparse | 0.9394 | 1.0000 | 0.9179 | 248.7ms |
| 4-ezis-hybrid-rrf | Hybrid BM25+BGE-M3 sparse (RRF) | 0.9134 | 0.9924 | 0.8860 | ~5ms |
| 4-ezis-hybrid-rrf-dense | Hybrid BM25+BGE-M3 dense (RRF) | 0.8967 | 0.9847 | 0.8677 | 379.8ms |
| 4-ezis-splade-ko | splade-ko (yjoonjang/splade-ko-v1) | 0.8998 | 0.9847 | 0.8733 | 106.67ms |
| 4-ezis-bgem3-sparse | BGE-M3 sparse (neural) | 0.8599 | 0.9847 | 0.8192 | 352.1ms |
| 4-ezis-bgem3-dense | BGE-M3 dense (cosine) | 0.8060 | 0.9351 | 0.7648 | ~350ms |

---

## 핵심 발견

### 1. MIRACL: BGE-M3 dense 단독이 여전히 최강 (NDCG@10=0.7915)
어떤 hybrid도 dense 단독을 이기지 못함. BM25 신호 품질이 dense보다 낮을 때, 융합이 오히려 dense 신호를 희석.

### 2. EZIS: Bayesian BM25+dense가 BM25 단독을 미세하게 능가 (0.9493 vs 0.9455)
양쪽 신호 모두 강한 도메인(기술 매뉴얼)에서는 Bayesian 융합이 BM25의 재현율 강점과 dense의 의미론적 강점을 결합해 소폭 이득.

### 3. Bayesian 융합 vs RRF 비교
| 데이터셋 | 조합 | Bayesian | RRF | 승자 |
|---------|------|---------|-----|------|
| MIRACL | BM25+sparse | 0.7485 | 0.7160 | **Bayesian** |
| MIRACL | BM25+dense | 0.7476 | 0.7527 | **RRF** |
| EZIS | BM25+sparse | 0.9394 | 0.9134 | **Bayesian** |
| EZIS | BM25+dense | 0.9493 | 0.8967 | **Bayesian** |

MIRACL BM25+dense에서 RRF > Bayesian인 이유: 두 신호 품질 격차가 클 때 Bayesian min-max equal-weight 정규화가 강한 신호(dense)를 희석. RRF는 위치 기반이므로 영향이 적음.

### 4. Sparse vs Dense 단독 비교
- MIRACL: dense (0.7915) > sparse (0.7634) — 의미론적 동의어 포착 강점
- EZIS: BM25 (0.9455) > sparse (0.8599) > dense (0.8060) — 정확한 용어 매칭 도메인

### 5. 가이드라인
| 데이터 특성 | 권장 방법 |
|------------|---------|
| 일반 위키/뉴스 (의미 다양) | BGE-M3 dense 단독 |
| 기술 매뉴얼/전문 용어 | BM25 단독 또는 Bayesian BM25+dense |
| 두 신호 모두 강함 | Bayesian 융합 (RRF보다 우수) |
| 두 신호 품질 격차 큼 | RRF 또는 강한 신호 단독 |

---

## Phase 간 비교 (MIRACL, 전체)

| Phase | 방법 | NDCG@10 |
|-------|------|---------|
| Phase 4 | BGE-M3 dense | **0.7915** |
| Phase 4 | BGE-M3 sparse | 0.7634 |
| Phase 4 | Hybrid BM25+dense RRF | 0.7527 |
| Phase 4 | Bayesian BM25+sparse | 0.7485 |
| Phase 4 | Bayesian BM25+dense | 0.7476 |
| Phase 4 | Hybrid BM25+sparse RRF | 0.7160 |
| Phase 4 | splade-ko (yjoonjang/splade-ko-v1) | 0.6962 |
| Phase 3 | pgvector-sparse BM25 kiwi-cong | 0.6326 |
| Phase 2 | pg_textsearch + MeCab (BM25/WAND) | 0.3374 |
| Phase 1 | Python BM25 kiwi-cong | 0.3471 |

---

## 출력

- `results/phase4/phase4_comparison.json` — 14개 실험 결과 (MIRACL 7 + EZIS 7)
- `experiments/phase4_bm25_vs_neural/phase4_comparison.py`
