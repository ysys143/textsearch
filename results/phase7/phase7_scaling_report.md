# Phase 7: PostgreSQL Scaling Comparison

**Generated:** 2026-03-25 19:20:56

---

## Latency p50 (ms)

| Scale | BM25 AND | BM25+Dense RRF | BM25+Dense Bayes | BM25 OR | VectorChord | pl/pgsql |
|-------|----------|---------------|-----------------|---------|-------------|---------|
|    1K |    0.4ms |     0.73ms |     0.73ms |   1.05ms |    1.1ms |   2.31ms |
|   10K |   0.42ms |     0.72ms |     0.72ms |   2.81ms |   1.35ms |  10.35ms |
|  100K |   0.62ms |     0.65ms |     0.65ms |  22.78ms |   3.58ms |  85.58ms |

## Latency p95 (ms)

| Scale | pg_textsearch AND | pg_textsearch OR | VectorChord-BM25 | pl/pgsql BM25 v2 |
|-------|------------------|-----------------|-----------------|-----------------|
|    1K |    0.48ms |    3.43ms |    2.12ms |    6.39ms |
|   10K |     0.6ms |   21.93ms |    2.54ms |   53.13ms |
|  100K |    1.61ms |   245.5ms |    7.18ms |  403.71ms |

## Index Build Time (s)

| Scale | pg_textsearch bm25 | pg_textsearch gin | VectorChord bm25 | pl/pgsql inv |
|-------|-------------------|------------------|-----------------|-------------|
|    1K |   0.15s |   0.02s |   0.25s |   1.63s |
|   10K |   1.34s |   0.12s |   1.05s |  15.43s |
|  100K |  12.27s |   1.61s |    1.2s | 171.04s |

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
