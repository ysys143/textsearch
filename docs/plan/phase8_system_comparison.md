# Phase 8: 외부 시스템 비교

**상태**: 계획 확정 (2026-03-25), Vespa Nori 추가 (2026-03-26)
**의존성**: Phase 7 완료 — PG 최선 스택 확정

---

## 목적

Phase 7에서 확정된 PostgreSQL 최선 스택을 외부 전문 검색 엔진과 동등 조건으로 비교한다.

**핵심 질문**: 한국어 하이브리드 검색에서 PostgreSQL이 전문 검색 엔진을 대체할 수 있는가?

---

## PostgreSQL 베이스라인 (Phase 7 확정)

| 구성요소 | 선택 | 근거 |
|---------|------|------|
| BM25 인덱스 | pg_textsearch `USING bm25` + `<@>` 연산자 | 10K/100K 스케일 모두 VectorChord-BM25보다 빠름 (Phase 7) |
| 한국어 토크나이저 | textsearch_ko (MeCab fork) | 형태소 분석, 조사/어미 제거 |
| Dense | pgvector HNSW cosine (BGE-M3 1024-dim) | Phase 6 MIRACL NDCG@10 0.7915 |
| 하이브리드 | DB-side RRF (k=60, SQL CTE) | Python-side 머지 없음 (사용자 요구사항) |

**Phase 7 실측값 (베이스라인)**:

| 방식 | MIRACL NDCG@10 | EZIS NDCG@10 | p50 |
|------|---------------|-------------|-----|
| BM25 | 0.6385 | 0.9162 | 0.44ms |
| Dense (retrieval-only*) | 0.7904 | 0.8041 | 1.2ms |
| RRF (retrieval-only*) | 0.7683 | 0.8641 | 1.79ms |
| Bayesian | 0.7272 | 0.9249 | 9.55ms |

> *retrieval-only: BGE-M3 인퍼런스 (~200ms) 제외, 쿼리 임베딩 사전 계산 조건

---

## 비교 대상 시스템 (Phase 8)

| 시스템 | BM25 토크나이저 | Dense | 하이브리드 | 포함 이유 |
|--------|--------------|-------|----------|---------|
| **Elasticsearch 8.x** | nori (형태소, MeCab 계열) | knn dense field | 내장 RRF | 검색 업계 표준, 한국어 형태소 지원 |
| **Qdrant 1.15.x** | multilingual (Unicode 경계, 비형태소*) | HNSW cosine | sparse+dense hybrid | 벡터 전용 DB 대표, 1.15 새 토크나이저 |
| **Vespa (ICU)** | ICU (비형태소, Unicode 경계) | HNSW angular | WAND+ANN native | 기본 토크나이저 |
| **Vespa (Nori)** | Lucene Nori (형태소, ES nori 동일) | HNSW angular | WAND+ANN native | Lucene Linguistics 내장 |

> **Weaviate 제외**: 내장 한국어 형태소 분석기 없음 — BM25 품질이 nori/MeCab 기반 시스템과 공정 비교 불가

> *Qdrant 1.15.x `"tokenizer": "multilingual"` = charabia Unicode word boundary.
> 형태소 분석 아님 — "먹었다" → 단일 토큰 (MeCab은 "먹+었+다" 분리).
> BM25 recall은 nori/MeCab보다 낮을 것으로 예상. 실측 예정.

---

## Qdrant 비교 모드

Qdrant는 두 가지 BM25 모드로 비교:

1. **Qdrant-builtin**: `tokenizer: multilingual` 내장 텍스트 인덱스 (Unicode, 비형태소)
2. **Qdrant-MeCab**: 외부 MeCab 토크나이징 후 sparse vector로 삽입 (SparseVectorParams, modifier="idf")

→ 1번은 "토크나이저 격차"를 실측, 2번은 "동등 조건 품질 비교" 목적

---

## 시스템별 설정

### Elasticsearch

```json
PUT /p8_es_miracl
{
  "settings": {
    "analysis": {
      "analyzer": {
        "korean": {
          "type": "nori",
          "decompound_mode": "mixed",
          "stoptags": ["E","IC","J","MAG","MM","SP","SSC","SSO","SC","SE","XPN","XSA","XSN","XSV","UNA","NA","VSV"]
        }
      }
    }
  },
  "mappings": {
    "properties": {
      "id":       { "type": "keyword" },
      "text":     { "type": "text", "analyzer": "korean" },
      "dense_vec":{ "type": "dense_vector", "dims": 1024, "index": true, "similarity": "cosine" }
    }
  }
}
```

- BM25 파라미터: ES 기본 (k1=1.2, b=0.75)
- Hybrid: `knn` + `multi_match` with RRF (`rank_window_size`: 60)

### Qdrant

```python
# 내장 텍스트 인덱스 (multilingual tokenizer)
client.create_payload_index(
    collection_name="p8_qdrant_miracl",
    field_name="text",
    field_schema=TextIndexParams(
        type="text",
        tokenizer=TokenizerType.MULTILINGUAL,
    )
)

# Sparse vector (MeCab 외부 토크나이징)
client.create_collection(
    "p8_qdrant_miracl_mecab",
    sparse_vectors_config={"bm25": SparseVectorParams(modifier="idf")}
)
```

- Dense: HNSW cosine, BGE-M3 1024-dim
- Qdrant 버전: 1.15.x (docker image: `qdrant/qdrant:v1.15.0`)

### Vespa

두 가지 토크나이저 모드로 비교:

1. **Vespa-ICU**: 기본 ICU 토크나이저 (비형태소, Unicode 경계 분리)
2. **Vespa-Nori**: Lucene Linguistics + Nori 분석기 (형태소, ES nori와 동일 Lucene 기반)

#### Vespa-ICU (기본)

```xml
<!-- services.xml -->
<services version="1.0">
  <container id="default" version="1.0">
    <search /><document-api />
  </container>
  ...
</services>
```

#### Vespa-Nori (Lucene Linguistics)

```xml
<!-- services.xml — Vespa >= 8.315.19 필수 -->
<services version="1.0" minimum-required-vespa-version="8.315.19">
  <container id="default" version="1.0">
    <component id="linguistics"
               class="com.yahoo.language.lucene.LuceneLinguistics"
               bundle="lucene-linguistics">
      <config name="com.yahoo.language.lucene.lucene-analysis"/>
    </component>
    <search /><document-api /><document-processing />
  </container>
  ...
</services>
```

```
schema doc {
  document doc {
    field doc_id type string { indexing: attribute | summary }
    field language type string { indexing: "ko" | set_language | summary }
    field text type string {
      indexing: summary | index
      index: enable-bm25
    }
    field dense_vec type tensor<float>(x[1024]) {
      indexing: attribute | index
      attribute { distance-metric: angular }
      index { hnsw { max-links-per-node: 16 neighbors-to-explore-at-insert: 200 } }
    }
  }
  rank-profile hybrid_rank inherits default {
    inputs { query(q_dense) tensor<float>(x[1024]) }
    first-phase { expression: 0.1 * bm25(text) + closeness(field, dense_vec) }
  }
}
```

- `lucene-linguistics` 번들은 Vespa 8.315.19+ 표준 Docker 이미지에 내장
- **그러나 `lucene-analysis-nori`(한국어)는 번들에 미포함**
- Maven으로 Nori JAR를 빌드하여 컴포넌트로 배포 시도 → OSGi 번들 격리로 실패
- ComponentsRegistry로 KoreanAnalyzer 등록 시도 → LuceneLinguistics가 인식 못함
- **결과: Vespa 표준 배포판에서는 한국어 형태소 분석 불가 → ICU 결과 유지**
- yahoojapan/vespa-kuromoji-linguistics처럼 별도 Linguistics 구현이 필요 (본 벤치마크 범위 외)

---

## 실험 매트릭스

### 데이터셋

| 데이터셋 | 문서 수 | 쿼리 수 | 특성 |
|---------|--------|--------|------|
| MIRACL-ko | 10,000 | 213 | Wikipedia 일반 도메인 |
| EZIS | 97 | 131 | 기술 매뉴얼 도메인 |

### 측정 방식별

| 방식 | 설명 |
|------|------|
| BM25-only | 텍스트 인덱스만 사용 |
| Dense-only | 벡터 인덱스만 사용 (동일 BGE-M3 임베딩) |
| Hybrid | BM25 + Dense 결합 (각 시스템 기본 방식) |

### 측정 지표

| 지표 | 설명 |
|------|------|
| NDCG@10 | 검색 품질 |
| Recall@10 | 재현율 |
| MRR | 첫 번째 적절 문서 순위 |
| p50 / p95 / p99 | 쿼리 레이턴시 (warm cache, 5회 워밍업) |
| 인덱스 빌드 시간 | 10K corpus 기준 |

---

## 환경

docker-compose.yml profiles (Phase 8 기준):

```bash
# PostgreSQL baseline (항상 실행)
docker compose --profile core up -d

# 비교 시스템 — 한 번에 하나씩 (fair latency)
docker compose --profile phase8-es up -d
docker compose --profile phase8-qdrant up -d
docker compose --profile phase8-vespa up -d
```

- `phase9-*` → `phase8-*` 로 프로파일 이름 변경 예정
- Vespa 서비스 추가 예정
- Qdrant: `qdrant/qdrant:latest` → `qdrant/qdrant:v1.15.0` 고정

---

## 출력

| 파일 | 내용 |
|------|------|
| `results/phase8/phase8_system_comparison.json` | 전체 결과 JSON |
| `results/phase8/phase8_system_comparison_report.md` | 종합 비교 리포트 |
| `experiments/phase8_system_comparison/phase8_es.py` | ES 벤치마크 |
| `experiments/phase8_system_comparison/phase8_qdrant.py` | Qdrant 벤치마크 |
| `experiments/phase8_system_comparison/phase8_vespa.py` | Vespa 벤치마크 |
| `experiments/phase8_system_comparison/phase8_report.py` | 통합 리포트 생성기 |

---

## 핵심 가설

| 시나리오 | 예상 |
|---------|------|
| BM25 품질 (MIRACL) | ES(nori) ≈ PG(MeCab) ≈ Vespa-Nori > Vespa-ICU ≈ Qdrant-builtin(Unicode) |
| BM25 품질 (EZIS) | PG(MeCab) ≈ ES(nori) ≈ Vespa-Nori >> Qdrant-builtin > Vespa-ICU |
| BM25 품질 Qdrant-MeCab | ≈ ES(nori) ≈ PG(MeCab) (토크나이저 동등 조건) |
| Vespa-Nori vs ES nori | ≈ 동일 (동일 Lucene Nori 분석기 사용) |
| Hybrid 품질 (MIRACL) | Vespa-Nori ≈ ES ≈ PG >> Vespa-ICU (ICU BM25 노이즈) |
| Query latency | PG < Qdrant < ES ≈ Vespa (in-process vs. HTTP) |
| 인덱스 빌드 | PG > Qdrant > ES (JVM warm-up, 분석 파이프라인 오버헤드) |
| 운영 복잡도 | PG(기존 인프라) << Qdrant < ES < Vespa |

---

## 주의사항

- Dense latency는 모든 시스템에서 retrieval-only (BGE-M3 인퍼런스 제외)
- 동일 BGE-M3 임베딩 사용 → Dense 품질 차이는 ANN 구현 차이에 기인
- Hybrid는 시스템별 기본 RRF 구현 사용 (파라미터 동일 조건 최대한 맞춤)
- 100K 스케일은 Phase 7에서 replica 방식 사용 — Phase 8에서도 동일 조건 적용
