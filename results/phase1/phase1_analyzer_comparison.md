# Phase 1: 형태소 분석기 비교 결과

Generated: 2026-03-23 14:40

Tokenizers: whitespace (baseline), Mecab, OKT, Kkma, kiwi-cong

Note: kiwi-knlm excluded (sj.knlm not bundled), khaiii excluded (ARM64 빌드 실패)


## MIRACL-ko (213 queries, 1000 docs, 135 have relevant in corpus)

| Tokenizer | NDCG@10 | Recall@10 | MRR | Self-R@1 | Throughput (docs/s) | Vocab | p50 ms |
|-----------|---------|-----------|-----|----------|---------------------|-------|--------|
| kiwi-cong | 0.3471 | 0.3854 | 0.4263 | 0.999 | 241 | 11829 | 6.94 |
| okt | 0.3333 | 0.3919 | 0.3982 | 1.0 | 62 | 17050 | 11.24 |
| kkma | 0.3268 | 0.3873 | 0.3898 | — | 5 | 13784 | 11.82 |
| mecab | 0.3147 | 0.3786 | 0.3719 | 1.0 | 587 | 14854 | 7.07 |
| whitespace | 0.2200 | 0.2556 | 0.2965 | 1.0 | 208605 | 32686 | 4.24 |

## EZIS Oracle Manual (131 queries, 97 chunks)

| Tokenizer | NDCG@10 | Recall@10 | MRR | Self-R@1 | Throughput (docs/s) | Vocab | p50 ms |
|-----------|---------|-----------|-----|----------|---------------------|-------|--------|
| kiwi-cong | 0.9455 | 1.0000 | 0.9267 | 0.9897 | 18 | 1678 | 2.27 |
| mecab | 0.9124 | 1.0000 | 0.8826 | 1.0 | 397 | 2087 | 1.0 |
| kkma | 0.9056 | 1.0000 | 0.8732 | — | 8 | 2137 | 8.39 |
| okt | 0.8982 | 1.0000 | 0.8635 | 1.0 | 40 | 2342 | 2.02 |
| whitespace | 0.8352 | 0.9427 | 0.8071 | 1.0 | 86334 | 3988 | 0.76 |

## 분석

**MIRACL top-3:** kiwi-cong > okt > kkma (by NDCG@10)

**EZIS top-3:** kiwi-cong > mecab > kkma (by NDCG@10)


### 속도/품질 트레이드오프
| Tokenizer | MIRACL NDCG@10 | Speed | 평가 |
|-----------|----------------|-------|------|
| kiwi-cong | 0.3471 | 241/s | 최고 품질, 실용적 속도 → **Phase 2/3 권장** |
| mecab | 0.3147 | 587/s | 속도/품질 최적점 → **Phase 2/3 권장** |
| okt | 0.3333 | 62/s | 좋은 품질, 느림 (JVM) |
| kkma | 0.3268 | 5/s | 매우 느림, 제외 권장 |
| whitespace | 0.2200 | 208k/s | 기준선만 |

→ **Phase 2, 3에서 사용할 형태소 분석기: kiwi-cong, mecab** (OKT는 선택적)
