# Phase 5: Korean Text Search Benchmark — Final Report

Generated: 2026-03-23 22:30 (updated 2026-03-25: Phase 5 production 10K + Phase 6 VectorChord results)

## MIRACL-ko Results (10k corpus, 213 queries)

| Phase | Method | NDCG@10 | R@10 | MRR | Latency p50 |
|-------|--------|---------|------|-----|-------------|
| phase4 | BGE-M3 dense (cosine) | 0.7915 | 0.9154 | 0.8013 | 253.01ms |
| phase4 | BGE-M3 sparse (neural) | 0.7634 | 0.8680 | 0.7830 | 156.92ms |
| phase4 | Hybrid BM25+BGE-M3 dense (RRF) | 0.7527 | 0.8907 | 0.7487 | 641.0ms |
| phase4 | Bayesian BM25+BGE-M3 sparse | 0.7485 | 0.8821 | 0.7492 | 290.94ms |
| phase4 | Bayesian BM25+BGE-M3 dense | 0.7476 | 0.8854 | 0.7442 | 379.33ms |
| phase4 | Hybrid BM25+BGE-M3 sparse (RRF) | 0.7160 | 0.8694 | 0.7060 | 119.08ms |
| phase4 | splade-ko (yjoonjang/splade-ko-v1) | 0.6962 | 0.8101 | 0.7174 | 104.67ms |
| **phase6-2** | **VectorChord-BM25 real-TF (textsearch_ko)** | **0.6415** | **0.8021** | **0.6194** | **0.97ms** |
| phase2 | pl/pgsql BM25 + MeCab (public.korean) | 0.6412 | 0.8012 | 0.6191 | 10.44ms |
| phase5 | pl/pgsql BM25 v2 real-TF (textsearch_ko) | 0.6414 | 0.8012 | 0.6199 | 11.34ms |
| phase5 | pg_textsearch AND (<@>) | 0.6401 | 0.8006 | 0.617 | 0.49ms |
| phase3 | pgvector-sparse BM25 (kiwi-cong) | 0.6326 | 0.7911 | 0.6195 | 4.24ms |
| phase4 | BM25 kiwi-cong (pgvector) | 0.6326 | 0.7911 | 0.6195 | 5.66ms |
| phase5 | pg_textsearch OR+WAND (<@>) | 0.6301 | 0.7885 | 0.614 | 0.82ms |
| phase6-1 | VectorChord-BM25 TF=1 (textsearch_ko) | 0.5888 | 0.7236 | 0.6034 | 1.01ms |
| phase3 | pgvector-sparse BM25 (Okt) | 0.5520 | 0.7120 | 0.5326 | 5.55ms |
| phase3 | pgvector-sparse BM25 (Mecab) | 0.5323 | 0.7066 | 0.5104 | 18.05ms |
| phase2 | pl/pgsql BM25 (pg_catalog.simple) | 0.4071 | 0.4757 | 0.4580 | 11.14ms |
| phase1 | kiwi-cong | 0.3471 | 0.3854 | 0.4263 | 6.94ms |
| phase2 | pg_textsearch + MeCab (BM25/WAND) | 0.3374 | 0.3844 | 0.4133 | 0.86ms |
| phase1 | okt | 0.3333 | 0.3919 | 0.3982 | 11.24ms |
| phase1 | kkma | 0.3268 | 0.3873 | 0.3898 | 11.82ms |
| phase1 | mecab | 0.3147 | 0.3786 | 0.3719 | 7.07ms |
| phase1 | kiwi-knlm | 0.2972 | 0.3661 | 0.3372 | 3.46ms |
| phase2 | pg_textsearch + korean_bigram (BM25/WAND) | 0.2642 | 0.3163 | 0.3213 | 1.08ms |
| phase2 | ParadeDB pg_search (BM25) | 0.2275 | 0.2605 | 0.3024 | 2.27ms |
| phase2 | pg_bigm (bigram) | 0.2266 | 0.2680 | 0.2902 | 5.9ms |
| phase2 | plpython3u+kiwipiepy | 0.2264 | 0.2449 | 0.2853 | 6.21ms |
| phase1 | whitespace | 0.2200 | 0.2556 | 0.2965 | 4.24ms |
| phase2 | pgroonga (Groonga FTS) | 0.1875 | 0.2429 | 0.2177 | 3.48ms |
| phase2 | textsearch_ko (MeCab) | 0.1815 | 0.1752 | 0.2432 | 0.73ms |
| phase2 | korean_bigram (C parser) | 0.0283 | 0.0276 | 0.0403 | 0.63ms |

## EZIS Results (97 docs, 131 queries)

| Phase | Method | NDCG@10 | R@10 | MRR |
|-------|--------|---------|------|-----|
| phase4 | Bayesian BM25+BGE-M3 dense | 0.9493 | 1.0000 | 0.9313 |
| phase1 | kiwi-cong | 0.9455 | 1.0000 | 0.9267 |
| phase3 | kiwi-cong BM25 | 0.9455 | 1.0000 | 0.9267 |
| phase4 | BM25 kiwi-cong (in-memory) | 0.9455 | 1.0000 | 0.9267 |
| phase2 | pl/pgsql BM25 + MeCab (public.korean) | 0.9290 | 0.9924 | 0.9085 |
| phase5 | pl/pgsql BM25 v2 real-TF (textsearch_ko) | 0.9290 | 0.9924 | 0.9085 |
| phase5 | pg_textsearch AND (<@>) | 0.9238 | 0.9924 | 0.9013 |
| **phase6-2** | **VectorChord-BM25 real-TF (textsearch_ko)** | **0.9238** | **0.9924** | **0.9013** |
| phase6-1 | VectorChord-BM25 TF=1 (textsearch_ko) | 0.9024 | 0.9847 | 0.8758 |
| phase4 | Bayesian BM25+BGE-M3 sparse | 0.9394 | 1.0000 | 0.9179 |
| phase1 | kiwi-knlm | 0.9160 | 1.0000 | 0.8874 |
| phase4 | Hybrid BM25+BGE-M3 sparse (RRF) | 0.9134 | 0.9924 | 0.8860 |
| phase1 | mecab | 0.9124 | 1.0000 | 0.8826 |
| phase3 | Mecab BM25 | 0.9124 | 1.0000 | 0.8826 |
| phase1 | kkma | 0.9056 | 1.0000 | 0.8732 |
| phase1 | okt | 0.8982 | 1.0000 | 0.8635 |
| phase3 | Okt BM25 | 0.8982 | 1.0000 | 0.8635 |
| phase4 | splade-ko (yjoonjang/splade-ko-v1) | 0.8998 | 0.9847 | 0.8733 |
| phase4 | Hybrid BM25+BGE-M3 dense (RRF) | 0.8967 | 0.9847 | 0.8677 |
| phase4 | BGE-M3 sparse (neural) | 0.8599 | 0.9847 | 0.8192 |
| phase2 | pl/pgsql BM25 (pg_catalog.simple) | 0.8567 | 0.9733 | 0.8237 |
| phase2 | pg_textsearch + MeCab (BM25/WAND) | 0.8417 | 0.9008 | 0.8224 |
| phase1 | whitespace | 0.8352 | 0.9427 | 0.8071 |
| phase3 | whitespace BM25 | 0.8352 | 0.9427 | 0.8040 |
| phase4 | BGE-M3 dense (cosine) | 0.8060 | 0.9351 | 0.7648 |
| phase2 | pg_textsearch + korean_bigram (BM25/WAND) | 0.8057 | 0.8931 | 0.7762 |
| phase2 | ParadeDB pg_search (BM25) | 0.7196 | 0.9046 | 0.6621 |
| phase2 | pg_bigm (bigram) | 0.5868 | 0.8206 | 0.5173 |
| phase2 | pgroonga (Groonga FTS) | 0.2481 | 0.4542 | 0.1841 |
| phase2 | plpython3u+kiwipiepy | 0.0924 | 0.2023 | 0.0603 |
| phase2 | textsearch_ko (MeCab) | 0.0076 | 0.0076 | 0.0076 |
| phase2 | korean_bigram (C parser) | 0.0000 | 0.0000 | 0.0000 |

## Key Findings

### MIRACL-ko
- **Best method**: BGE-M3 dense (cosine) — NDCG@10=0.7915
- **Best BM25-only**: 0.6414~0.6415 수렴 — pl/pgsql v2, pg_textsearch AND, VectorChord(Phase 6-2) 모두 동등
- **Phase 6-2 (VectorChord real-TF)**: NDCG=0.6415, p50=**0.97ms** — pl/pgsql BM25 v2(11.34ms) 대비 **11.7배** 빠름, 동일 정확도
- **pg_textsearch AND**: NDCG=0.6401, p50=**0.49ms** — BM25보다 약간 낮지만 가장 빠름
- **Phase 6-1 (TF=1)**: NDCG=0.5888 — tsvector_to_array()의 TF 손실로 0.053 하락 확인
- **BM25 알고리즘은 동일**: Phase 5 1K→10K corpus 교정 후 모든 BM25 구현이 0.63~0.64로 수렴
- pgvector-sparse (Phase 3) vs Python BM25 (Phase 1): +82.3% NDCG gain from DB indexing
- **splade-ko latency**: re-measured with pgvector sparsevec exact scan → p50 555ms→104.67ms (5.3×); bottleneck is MPS model inference (~100ms), not DB search

### EZIS
- **Best method**: Bayesian BM25+BGE-M3 dense — NDCG@10=0.9493
- **Best BM25-only**: pl/pgsql BM25 + kiwi-cong — NDCG@10=0.9455 (Phase 1/3)
- **Phase 6-2 (VectorChord real-TF)**: NDCG=0.9238, p50=0.97ms — pl/pgsql BM25 v2(0.9290)와 0.005 차이, 레이턴시 2.34ms→0.97ms
- MeCab morphological analysis inside pl/pgsql is the key differentiator for both datasets
