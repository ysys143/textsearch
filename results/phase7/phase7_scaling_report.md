# Phase 7: PostgreSQL 3-way Scaling Comparison

**Generated:** 2026-03-25 18:19:58

---

## Latency p50 (ms)

| Scale | pg_textsearch AND | pg_textsearch OR | VectorChord-BM25 | pl/pgsql BM25 v2 |
|-------|------------------|-----------------|-----------------|-----------------|
|    1K |    0.44ms |    1.16ms |     1.1ms |    2.31ms |
|   10K |    0.47ms |    3.03ms |    1.35ms |   10.35ms |
|  100K |    0.96ms |   24.23ms |    3.58ms |   85.58ms |

## Latency p95 (ms)

| Scale | pg_textsearch AND | pg_textsearch OR | VectorChord-BM25 | pl/pgsql BM25 v2 |
|-------|------------------|-----------------|-----------------|-----------------|
|    1K |    0.69ms |    3.76ms |    2.12ms |    6.39ms |
|   10K |    0.67ms |   22.36ms |    2.54ms |   53.13ms |
|  100K |    3.52ms |  232.35ms |    7.18ms |  403.71ms |

## Index Build Time (s)

| Scale | pg_textsearch bm25 | pg_textsearch gin | VectorChord bm25 | pl/pgsql inv |
|-------|-------------------|------------------|-----------------|-------------|
|    1K |   0.13s |   0.02s |   0.25s |   1.63s |
|   10K |   1.32s |   0.12s |   1.05s |  15.43s |
|  100K |  12.49s |   1.71s |    1.2s | 171.04s |

## Index Size

| Scale | pg_textsearch bm25 | pg_textsearch gin | VectorChord | pl/pgsql inv |
|-------|-------------------|------------------|------------|-------------|
|    1K |     912 kB |    1024 kB |     106 MB |    5896 kB |
|   10K |    4128 kB |    4976 kB |     395 MB |      49 MB |
|  100K |      18 MB |      23 MB |     498 MB |     501 MB |

---

## 요약

- **pg_textsearch AND**: BM25 인덱스, `<@>` 연산자 (AND matching)
- **pg_textsearch OR**: GIN 인덱스 + ts_rank_cd (OR matching)
- **VectorChord-BM25**: Block-WeakAnd posting list (Phase 6-3 실측)
- **pl/pgsql BM25 v2**: B-tree 역인덱스, real-TF (Phase 6-3 실측)
