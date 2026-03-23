# Phase 1: 한국어 형태소 분석기 비교

## 목표

Phase 2~4에서 사용할 tokenizer를 결정하기 위한 독립 벤치마크.
BM25 파이프라인 안에서 각 형태소 분석기의 검색 품질과 속도를 직접 비교한다.

## 의존성

- Phase 0 완료 (MIRACL-ko + EZIS QA set)

---

## 실험 대상

| ID | 분석기 | 기반 | 비고 |
|----|--------|------|------|
| 1-A | whitespace | 공백 분리 | 기준선 |
| 1-B | Mecab | C extension, 사전 기반 | 속도 기준선 |
| 1-C | OKT (Open Korean Text) | konlpy, Java 기반 | |
| 1-D | Kkma | konlpy, Java 기반, 고품질/저속 | |
| 1-E | kiwi-cong | Transformer 기반 신경망 형태소 | |
| 1-F | kiwi-knlm | 언어 모델 기반 | `sj.knlm` 파일 필요 |
| 1-G | khaiii | Kakao, C extension | ARM64 빌드 시도 필요 |

---

## 실험 방법

Python-side BM25 (`BM25Embedder`) + 각 tokenizer 조합으로 동일한 corpus에 대해 검색 수행.
tokenizer만 변수로 고정, BM25 파라미터(k1=1.5, b=0.75)는 동일하게 유지.

```python
for tokenizer in [whitespace, mecab, okt, kkma, kiwi_cong, kiwi_knlm, khaiii]:
    embedder = BM25Embedder(tokenizer=tokenizer)
    embedder.fit(corpus)
    ndcg, recall, mrr = evaluate(embedder, queries, qrels)
    throughput = measure_throughput(tokenizer, corpus)
```

---

## 평가 지표

| 지표 | 설명 |
|------|------|
| NDCG@10 | 주요 품질 지표 |
| Recall@10 | 재현율 |
| MRR | Mean Reciprocal Rank |
| throughput | 토크나이징 docs/s |
| vocab_size | 고유 토큰 수 |
| latency p50 | 쿼리당 임베딩 시간 |

두 데이터셋(MIRACL-ko, EZIS) 각각 평가.

---

## khaiii ARM64 빌드 계획

```bash
# Docker 내에서 빌드 시도
git clone https://github.com/kakao/khaiii.git
cd khaiii && mkdir build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release
make && make resource
make install
```

빌드 실패 시: ARM64 미지원으로 결론, 실험에서 제외 후 기록.

---

## 출력

- `results/phase1/phase1_analyzer_comparison.json` — 전체 결과
- `results/phase1/phase1_analyzer_comparison.md` — 요약 리포트

## 다음 단계

- NDCG@10 기준 top-3 tokenizer → Phase 2, 3에서 사용
- 속도/품질 트레이드오프 분석 → Phase 5 production 추천에 반영
