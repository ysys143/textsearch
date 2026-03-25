# Phase 8: 외부 시스템 비교

## 목적

Phase 7에서 PostgreSQL 내부 옵션 최선 세팅이 확정되면,
동일 데이터셋(MIRACL-ko 10K + EZIS)으로 외부 시스템과 BM25 동등 조건 비교.

**핵심 질문**: PostgreSQL BM25가 전문 검색 엔진(ES, Qdrant)과 얼마나 차이 나는가?

---

## 비교 대상

| 시스템 | BM25 방식 | 한국어 토크나이저 |
|--------|----------|----------------|
| PostgreSQL (Phase 7 최선) | VectorChord or pg_textsearch | textsearch_ko (MeCab) |
| Elasticsearch 8.x | Lucene BM25 | nori (형태소) |
| Qdrant | BM25 sparse vectors | tokenizer 설정 필요 |
| Weaviate | BM25F | tokenizer 설정 필요 |

---

## 측정 항목

- NDCG@10, Recall@10, MRR (MIRACL-ko 10K, 213 queries)
- Query latency p50/p95
- Index build time
- 운영 복잡도 (설정/운영 비용 정성 평가)

---

## 환경

docker-compose.yml에 이미 정의됨:
- `--profile phase9-es`: Elasticsearch 8.11.0 (port 9200)
- `--profile phase9-qdrant`: Qdrant (port 6333)
- `--profile phase9-weaviate`: Weaviate (port 8080)

---

## 출력

- `results/phase8/phase8_system_comparison_report.md`
- `results/phase8/phase8_system_comparison.json`
