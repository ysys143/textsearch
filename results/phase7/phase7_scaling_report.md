# Phase 7: PostgreSQL Scaling Comparison

**Generated:** 2026-03-25 19:15:05

---

## Latency p50 (ms)

| Scale | BM25 AND | BM25+Dense RRF | BM25+Dense Bayes | BM25 OR | VectorChord | pl/pgsql |
|-------|----------|---------------|-----------------|---------|-------------|---------|
|    1K |   0.47ms |     0.71ms |     0.71ms |   1.07ms |    1.1ms |   2.31ms |
|   10K |   0.44ms |     0.71ms |     0.71ms |   2.77ms |   1.35ms |  10.35ms |
|  100K |   0.71ms |     0.67ms |     0.66ms |  22.58ms |   3.58ms |  85.58ms |

## Latency p95 (ms)

| Scale | pg_textsearch AND | pg_textsearch OR | VectorChord-BM25 | pl/pgsql BM25 v2 |
|-------|------------------|-----------------|-----------------|-----------------|
|    1K |    0.63ms |    3.14ms |    2.12ms |    6.39ms |
|   10K |     0.6ms |   21.39ms |    2.54ms |   53.13ms |
|  100K |    2.35ms |  229.23ms |    7.18ms |  403.71ms |

## Index Build Time (s)

| Scale | pg_textsearch bm25 | pg_textsearch gin | VectorChord bm25 | pl/pgsql inv |
|-------|-------------------|------------------|-----------------|-------------|
|    1K |   0.14s |   0.02s |   0.25s |   1.63s |
|   10K |   1.26s |   0.14s |   1.05s |  15.43s |
|  100K |  13.55s |   2.25s |    1.2s | 171.04s |

## Index Size

| Scale | pg_textsearch bm25 | pg_textsearch gin | VectorChord | pl/pgsql inv |
|-------|-------------------|------------------|------------|-------------|
|    1K |     912 kB |    1024 kB |     106 MB |    5896 kB |
|   10K |    4136 kB |    4992 kB |     395 MB |      49 MB |
|  100K |      18 MB |      23 MB |     498 MB |     501 MB |

---

## 요약

- **pg_textsearch AND**: BM25 인덱스, `<@>` 연산자 (AND matching)
- **pg_textsearch OR**: GIN 인덱스 + ts_rank_cd (OR matching)
- **VectorChord-BM25**: Block-WeakAnd posting list (Phase 6-3 실측)
- **pl/pgsql BM25 v2**: B-tree 역인덱스, real-TF (Phase 6-3 실측)
