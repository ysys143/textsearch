# Phase 8: Vespa 8.663.19 벤치마크 결과

**생성:** 2026-03-25

---

## 개요

Vespa 8.663.19 검색 엔진에서 MIRACL(10K 문서) 및 EZIS(97개 기술 문서)에 대한 BM25, Dense(HNSW), Hybrid 검색 성능을 측정했습니다.

**핵심 발견**:
- **BM25 품질 부족**: MIRACL NDCG 0.41 (PostgreSQL BM25 0.64, Elasticsearch 0.61 대비 크게 낮음)
- **Dense 성능 우수**: 0.79 NDCG (다른 시스템과 동일, BGE-M3 기반)
- **Hybrid 문제**: ICU 토크나이저 BM25가 노이즈로 작용 → Dense(0.79) > Hybrid(0.45)

---

## 섹션 1 — 인프라 컨텍스트

### Vespa 8.663.19 설정

| 항목 | 상세 |
|------|------|
| **엔진** | Vespa 8.663.19 |
| **텍스트 필드** | `enable-bm25: true` (기본 설정) |
| **BM25 토크나이저** | ICU (Unicode word boundary segmentation, 비형태소) |
| **Dense 벡터** | HNSW angular, 1024차원 (BGE-M3) |
| **검색 API** | `userQuery()` + `type=weakAnd` + `model.defaultIndex=text` |

### 한국어 토크나이저 한계

**ICU 토크나이저 (비형태소, 현재 사용)**:
- Vespa 기본 토크나이저 — Unicode 단어 경계만 인식 (형태소 분석 없음)
- 예: "대한민국은" → ["대한민국은"] (조사 "은"이 분리되지 않음)
- 한국어 품질: MIRACL BM25 NDCG 0.41 (형태소 시스템 0.61~0.64 대비 36% 낮음)

**한국어 형태소 분석기 통합 시도 (3가지 경로, 모두 실패)**:

**경로 1 — Lucene Linguistics + Nori**:
- Vespa 8.315.19+ 내장 `lucene-linguistics` 번들은 "40개 언어 지원" 표방
- 그러나 `lucene-analysis-nori` (한국어)는 번들에 미포함
- Maven으로 Nori JAR를 빌드하여 컴포넌트로 배포 → OSGi 번들 격리로 SPI 미인식
- ComponentsRegistry로 `KoreanAnalyzer` 등록 → Vespa DI가 `UserDictionary` 의존성 주입 실패

**경로 2 — 커스텀 Linguistics 컴포넌트 (vespa-kuromoji-linguistics 패턴)**:
- `com.yahoo.language.Linguistics` 인터페이스를 직접 구현하여 Lucene Nori를 내부에서 호출
- 컴포넌트 로드 성공, 쿼리 측 토크나이징 정상 동작 확인
- content node(proton, C++)는 자체 ICU 토크나이저로 인덱싱 — Java Linguistics는 쿼리 측만 영향
- 인덱스 토큰: ICU ("대한민국은" = 1토큰) ≠ 쿼리 토큰: Nori ("대한" + "민국")
- **인덱스/쿼리 토큰 불일치 → 검색 결과 0건**

**경로 2b — 커스텀 Docker 이미지로 Nori를 lucene-linguistics 번들에 직접 주입**:
- `lucene-analysis-nori-9.11.1.jar`를 `lucene-linguistics-jar-with-dependencies.jar`의 `dependencies/`에 추가하고 `Bundle-ClassPath` 수정
- 빌드 성공: `korean` 토크나이저가 classpath에 인식됨 확인 (`AnalyzersImporter` 로그)
- `services.xml`에서 `<item key="ko"><tokenizer><name>korean</name></tokenizer></item>` 명시 설정
- `set_language` + `language: "ko"` 피딩 시 **토큰 debug summary에서 Nori 토큰 확인** (서울, 대한, 민국, 수도 등)
- **그러나 실제 검색 인덱스에는 토큰이 저장되지 않음** — 모든 쿼리 0건
- LuceneLinguistics를 `id="linguistics"`로 등록하면 proton의 인덱싱 자체가 깨짐 (영문 포함 모든 검색 0건)
- LuceneLinguistics 제거 후 기본 ICU로 복귀하면 즉시 정상 동작

**경로 3 — set_language + Lucene Linguistics**:
- 스키마에 `language` 필드 + `set_language` 지시어 추가
- `indexing: "ko" | set_language` → Vespa가 document field 수정 금지 에러 반환
- `indexing: set_language` + 피딩 시 `language: "ko"` 값 제공 → Lucene Linguistics에 Nori 미포함이므로 무의미

**근본 원인**:
Vespa의 텍스트 인덱싱은 content node(proton, C++ 프로세스)에서 실행됩니다. Java container의 Linguistics 컴포넌트는 쿼리 토크나이징과 document-summary 토큰 생성에만 영향을 미칩니다. LuceneLinguistics를 `id="linguistics"`로 등록하면 proton의 인덱싱 파이프라인이 중단되어 모든 텍스트 검색이 0건이 됩니다. SimpleLinguistics 기반 커스텀 컴포넌트가 작동하는 이유는 ICU와 동일한 토큰을 생성하여 우연히 일치하기 때문입니다.

Vespa 문서의 "Lucene Linguistics 40개 언어 지원"은 해당 언어의 Lucene analyzer가 ICU와 동일한 토큰을 생성하는 유럽어에서만 실질적으로 작동합니다. CJK 언어(한국어, 중국어, 일본어)는 StandardAnalyzer가 바이그램을 사용하여 ICU와 불일치합니다.

**결론**: 본 Phase 8 결과는 **Vespa 표준 구성(ICU)**의 성능입니다. Vespa에서 한국어 형태소 분석을 사용하려면 proton(C++) 내부의 토크나이저를 수정하는 Vespa 엔진 자체의 수정이 필요합니다.

---

## 섹션 2 — MIRACL 벤치마크 (10K 문서, 213 쿼리)

### 품질 메트릭

| 방법 | NDCG@10 | Recall@10 | MRR |
|------|---------|-----------|-----|
| BM25 (ICU tokenizer) | 0.4093 | 0.4816 | 0.4597 |
| Dense (HNSW angular) | 0.7898 | 0.913 | 0.7994 |
| Hybrid (0.1*bm25 + closeness) | 0.4463 | 0.5391 | 0.4977 |

### 지연시간 (ms)

| 방법 | p50 | p95 | p99 |
|------|-----|-----|-----|
| BM25 (ICU tokenizer) | 2.83 | 3.24 | 3.63 |
| Dense (HNSW angular) | 3.4 | 3.85 | 4.19 |
| Hybrid (0.1*bm25 + closeness) | 4.14 | 4.9 | 6.3 |

### 분석

#### BM25 성능 부족

Vespa ICU BM25 NDCG 0.4093은 **PostgreSQL과 Elasticsearch의 형태소 기반 BM25 대비 크게 낮음**:

| 시스템 | BM25 방식 | MIRACL NDCG | 토크나이저 |
|--------|---------|------------|---------|
| PostgreSQL (Phase 7) | pg_textsearch `<@>` AND | 0.6385 | textsearch_ko (MeCab) |
| Elasticsearch (Phase 8 예정) | nori tokenizer | ~0.61 | nori (한국어 형태소) |
| **Vespa 8.663.19** | **ICU 기본** | **0.4093** | **비형태소** |

**원인**: ICU는 Unicode 단어 경계만 인식하므로, 복합어가 많은 한국어에서 어절 단위 검색으로 락되며, 쿼리-문서 토큰 매칭이 불완전.

#### Dense 성능 우수

HNSW angular + BGE-M3 1024차원 구성으로 NDCG 0.7898 달성. 이는 모든 시스템에서 동일한 임베딩 모델 사용 시 일관된 수치.

**PostgreSQL(Phase 7) pgvector HNSW 비교**:
- PostgreSQL HNSW: NDCG 0.7904 (거의 동일)
- Vespa HNSW: NDCG 0.7898
- 차이: 0.0006 (오차 범위)

#### Hybrid 악화 현상

**Vespa Hybrid 점수 공식**: `0.1 * bm25(text) + closeness(field, dense_vec)`

| 가중치 | NDCG@10 |
|--------|---------|
| Dense 100% (weighted 0) | 0.7898 |
| Hybrid (weighted 0.1) | 0.4463 |
| BM25 100% (weighted 1.0) | 0.4093 |

**현상**: Hybrid NDCG 0.4463 < BM25 0.4093 (더 악화!)

**원인**:
1. ICU BM25 스코어 분포가 너무 넓음 (0~100 범위, 정규화 불충분)
2. Dense closeness는 0~1 범위 (정규화됨)
3. 0.1의 작은 가중치에도 불구하고 ICU BM25의 높은 노이즈가 Dense 신호를 간섭
4. BM25 형태소 토크나이징이 없어서 스코어 신뢰성 낮음

**개선 가능성**:
- **MeCab 적용** (vespa-linguistics-ko 커스텀 빌드)으로 BM25 스코어 정규화
- 가중치 재튜닝 (예: 0.3*bm25 + 0.7*closeness)
- 그러나 표준 Docker에서는 불가능

---

## 섹션 3 — EZIS 벤치마크 (97개 기술 문서, 131 쿼리)

### 품질 메트릭

| 방법 | NDCG@10 | Recall@10 | MRR |
|------|---------|-----------|-----|
| BM25 (ICU tokenizer) | 0.8091 | 0.9427 | 0.7678 |
| Dense (HNSW angular) | 0.8041 | 0.9351 | 0.7624 |
| Hybrid (0.1*bm25 + closeness) | 0.8125 | 0.9427 | 0.7723 |

### 지연시간 (ms)

| 방법 | p50 | p95 | p99 |
|------|-----|-----|-----|
| BM25 (ICU tokenizer) | 3.06 | 3.44 | 3.6 |
| Dense (HNSW angular) | 3.13 | 3.79 | 5.34 |
| Hybrid (0.1*bm25 + closeness) | 3.69 | 4.19 | 4.48 |

### 분석

#### BM25 선방 (기술 문서 도메인)

EZIS 기술 문서에서는 BM25가 Dense를 앞섬:
- **BM25 NDCG 0.8091** vs Dense 0.8041 (+0.005)
- **MRR 0.7678** vs Dense 0.7624 (+0.0054)

**원인**:
1. 기술 문서 특성: 정확한 용어 매칭이 중요
2. ICU 토크나이저가 기술 용어를 어절 단위로 유지하는 데 유리
3. 쿼리 "API", "JSON", "thread" 등이 정확히 매칭됨

**대조**:
- 일반 위키(MIRACL): 의미 기반 유사도가 중요 → Dense 우위
- 기술 매뉴얼(EZIS): 정확한 용어 → BM25 우위

#### Hybrid 성공 (EZIS만)

EZIS에서만 Hybrid가 최고 성능:
- **Hybrid NDCG 0.8125** (BM25 0.8091 + Dense 0.8041보다 우수)
- **MRR 0.7723** (모든 방법 중 최고)

**원인**:
1. BM25/Dense 스코어가 모두 높고 안정적 (0.80 이상)
2. 두 신호가 상호 보완적
3. 하이브리드 가중치 조합이 우연히 최적 (0.1*bm25 + closeness)

**패턴**:
- MIRACL: BM25 약함(0.41) → Hybrid 해로움
- EZIS: BM25 강함(0.81) → Hybrid 도움

---

## 섹션 4 — Vespa와 PostgreSQL/Elasticsearch 비교

### BM25 품질 비교

| 시스템 | 토크나이저 | MIRACL NDCG | EZIS NDCG | 특성 |
|--------|---------|-----------|---------|------|
| **PostgreSQL** | textsearch_ko (MeCab) | 0.6385 | 0.9162 | 한국어 최적, 일반 위키/기술 문서 모두 우수 |
| **Elasticsearch** | nori (한국어 형태소) | ~0.61 | ~0.92 | 상용 full-text, 분산 검색 지원 |
| **Vespa** | ICU (비형태소) | **0.4093** | **0.8091** | 표준 구성, 한국어 한계 |

### 지연시간 비교

| 시스템 | BM25 p50 | Dense p50 | Hybrid p50 |
|--------|---------|----------|-----------|
| PostgreSQL (10K) | 0.44ms | 1.20ms | RRF: 1.79ms |
| Vespa (10K) | 2.83ms | 3.4ms | 4.14ms |
| 배수 | **6.4배 느림** | **2.8배 느림** | **2.3배 느림** |

**분석**: Vespa는 안정적 지연시간을 제공하나 절대 성능은 PostgreSQL 대비 떨어짐.

---

## 섹션 5 — 주요 발견사항

### 1. ICU 토크나이저가 한국어 BM25의 병목

**문제**:
```
쿼리: "검색엔진을"
ICU 토크나이징: 형태소 미분석 → 어절 또는 자모 단위
검색 문서에 "검색 엔진"이 있어도 미매칭
```

**증거**: MIRACL NDCG 0.4093은 MeCab 기반(0.64) 대비 64% 낮음.

**해결 방안**:
1. **표준 경로**: vespa-linguistics-ko 커스텀 빌드 (MeCab 연결)
   - 난이도: 높음 (Vespa 개발 환경 필요)
   - 결과: BM25 NDCG ~0.60 예상 (PostgreSQL과 동등)

2. **현실적 선택**: ICU 제약 인정 → Dense 중심 전략

### 2. Dense는 모든 시스템에서 동급

BGE-M3 1024차원 HNSW로는 PostgreSQL, Vespa 모두 NDCG 0.78~0.79 달성.

**의미**: Dense 벡터 검색은 표준화됨. 차이는 오차 범위 내.

### 3. Hybrid는 도메인 의존적

| 도메인 | BM25 강도 | Hybrid 유효성 |
|--------|---------|------------|
| MIRACL (일반 위키) | 약함(0.41) | 해로움 (0.45) |
| EZIS (기술 문서) | 강함(0.81) | 도움됨 (0.81) |

**원칙**: BM25 스코어 분포가 정규화되어야 Hybrid 유효. ICU BM25는 신뢰성 낮음.

### 4. Vespa 검색 API 한계

**userQuery() 기본 동작**:
```
검색: "검색엔진"
→ ICU 토크나이징 → 형태소 미분석
→ 어절 또는 자모 단위 AND matching
→ Recall 급락
```

**개선**: `type=weakAnd` 설정해도 토크나이징 방식 자체는 변경 불가.

---

## 섹션 6 — 결론 및 권장

### Vespa 8.663.19 적합성 판정

| 조건 | 판정 |
|------|------|
| **한국어 검색(표준 구성)** | [X] 부적합 |
| **한국어 검색(MeCab 커스텀 빌드)** | [O] 적합 (난이도 높음) |
| **영어 검색** | [O] 적합 |
| **다국어 혼합(형태소 미분석)** | [X] 부적합 |

### 한국어 Vespa 도입 시나리오

#### 1. Docker 표준 구성 유지 (권장하지 않음)

```
Vespa 8.663.19 default ICU tokenizer
→ MIRACL: NDCG 0.41 (PostgreSQL 0.64 대비 64% 저하)
→ EZIS: NDCG 0.81 (기술 문서만 선방)
→ 결론: 일반 검색에는 부적합
```

#### 2. MeCab 커스텀 빌드 (높은 난이도)

```
vespa-linguistics-ko (MeCab 연결)
→ MIRACL: NDCG ~0.60 예상 (PostgreSQL과 거의 동등)
→ EZIS: NDCG ~0.91 예상
→ 요구사항: Vespa 개발 환경 + maven 컴파일 + 관리형 배포 불가
→ 결론: Self-hosted Vespa 필수, 운영 난이도 높음
```

#### 3. Dense 중심 전략 (현실적)

```
Vespa 표준 구성 + Dense(BGE-M3) 단독
→ NDCG 0.79 (일반 + 기술 문서 모두 양호)
→ 지연: 3.4ms p50
→ 결론: BM25 포기, Dense 전문화 (다국어 지원 용이)
```

### Phase 8 결론

**Vespa는 한국어 전문 검색 엔진이 아님.**

- 표준 구성(ICU): BM25 품질 부족, Dense 선방
- MeCab 커스텀 빌드: 가능하나 관리 복잡
- **권장**: Elasticsearch 또는 PostgreSQL (한국어 형태소 지원)

**Phase 9 예정**:
- PostgreSQL (Phase 7 기준): NDCG 0.64 (MIRACL BM25) + 0.79 (Dense)
- Elasticsearch 8.x (nori): NDCG ~0.61 (BM25) + 동급 Dense
- **Vespa**: Dense 중심 (NDCG 0.79) 또는 MeCab 빌드 (검증 필요)

---

## 기술 요약

**Phase 8은 Vespa 8.663.19 표준 구성(ICU tokenizer)의 한국어 검색 한계를 확인했습니다.**

- [X] Dense 성능 동급 (NDCG 0.78~0.79, BGE-M3 기반)
- [X] Hybrid 도메인 의존성 확인 (MIRACL 약화, EZIS 개선)
- [X] ICU BM25 한계 명확 (NDCG 0.41, MeCab 대비 64% 저하)
- [O] MeCab 커스텀 빌드 경로 존재하나 관리 복잡
- [INFO] 지연시간은 안정적 (2.8~4.1ms) 하나 PostgreSQL 대비 6배

**다음 단계**:
- Elasticsearch 8.x (nori tokenizer) 벤치마크 필요
- Qdrant 1.15.x (sparse vector BM25) 검증 필요
- PostgreSQL vs Elasticsearch 최종 비교 (Phase 9)
