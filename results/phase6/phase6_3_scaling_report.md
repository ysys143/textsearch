# Phase 6-3: VectorChord-BM25 vs pl/pgsql BM25 v2 — Scaling Test

**Generated:** 2026-03-25 17:36:52

---

## Latency vs Scale

### VectorChord-BM25 (bm25vector + Block-WeakAnd)

| Scale | Insert | Index Build | Index Size | p50 | p95 |
|-------|--------|-------------|------------|-----|-----|
| 1,000 | 0.66s | 0.25s | 106 MB | 1.1ms | 2.12ms |
| 10,000 | 6.1s | 1.05s | 395 MB | 1.35ms | 2.54ms |
| 100,000 | 63.08s | 1.2s | 498 MB | 3.58ms | 7.18ms |

### pl/pgsql BM25 v2 (inverted_index B-tree + stats tables)

| Scale | Insert | Index Build | Index Size | p50 | p95 |
|-------|--------|-------------|------------|-----|-----|
| 1,000 | 0.19s | 1.63s | 5896 kB | 2.31ms | 6.39ms |
| 10,000 | 1.54s | 15.43s | 49 MB | 10.35ms | 53.13ms |
| 100,000 | 19.0s | 171.04s | 501 MB | 85.58ms | 403.71ms |

---

## EXPLAIN ANALYZE (10K docs)

### VectorChord-BM25

```
Limit  (cost=0.00..0.64 rows=10 width=36) (actual time=0.734..0.751 rows=10.00 loops=1)
  Buffers: shared hit=4007
  ->  Index Scan using t6_scale_10k_emb_idx on t6_scale_10k  (cost=0.00..643.94 rows=9995 width=36) (actual time=0.733..0.750 rows=10.00 loops=1)
        Order By: (emb <&> '(t6_scale_10k_emb_idx,"{89:1, 454:1, 783:1, 1298:1, 1344:1, 1882:2, 1980:1, 4791:1}")'::bm25query)
        Index Searches: 0
        Buffers: shared hit=4007
Planning Time: 0.026 ms
Execution Time: 0.765 ms
```

### pl/pgsql BM25 v2

```
Limit  (cost=0.25..0.35 rows=10 width=32) (actual time=36.541..36.542 rows=10.00 loops=1)
  Buffers: shared hit=2863
  ->  Function Scan on bm25_scale_ranking  (cost=0.25..10.25 rows=1000 width=32) (actual time=36.540..36.541 rows=10.00 loops=1)
        Buffers: shared hit=2863
Planning Time: 0.012 ms
Execution Time: 36.547 ms
```

