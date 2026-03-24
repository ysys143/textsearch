"""Phase 5: Elasticsearch (nori BM25) vs Qdrant (sparse BM25) benchmark.

Compares against PostgreSQL best settings from phases 1-4.

Usage:
    source .venv/bin/activate && python3 experiments/phase5_system_comparison/phase5_es_qdrant.py \
        --es-url http://localhost:9200 \
        --qdrant-url http://localhost:6333 \
        --output-dir results/phase5
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def ndcg(ranked: list[str], rel: set[str], k: int = 10) -> float:
    dcg  = sum(1 / math.log2(r + 2) for r, d in enumerate(ranked[:k]) if d in rel)
    idcg = sum(1 / math.log2(i + 2) for i in range(min(len(rel), k)))
    return dcg / idcg if idcg > 0 else 0.0

def recall(ranked: list[str], rel: set[str], k: int = 10) -> float:
    return len(set(ranked[:k]) & rel) / len(rel) if rel else 0.0

def mrr(ranked: list[str], rel: set[str], k: int = 10) -> float:
    for r, d in enumerate(ranked[:k]):
        if d in rel:
            return 1 / (r + 1)
    return 0.0

def evaluate(search_fn, queries: list[dict], k: int = 10, n_warm: int = 5) -> dict:
    """Run evaluation + latency benchmark. search_fn(text) -> list[str] of doc ids."""
    # Warmup
    for q in queries[:n_warm]:
        search_fn(q["text"])

    ndcgs, recalls, mrrs, lats = [], [], [], []
    for q in queries:
        rel = set(str(r) for r in q.get("relevant_ids", []))
        if not rel:
            continue
        t0 = time.perf_counter()
        hits = search_fn(q["text"])
        lats.append((time.perf_counter() - t0) * 1000)
        ndcgs.append(ndcg(hits, rel, k))
        recalls.append(recall(hits, rel, k))
        mrrs.append(mrr(hits, rel, k))

    lats_sorted = sorted(lats)
    return {
        "ndcg_at_10": round(sum(ndcgs) / len(ndcgs), 4),
        "recall_at_10": round(sum(recalls) / len(recalls), 4),
        "mrr": round(sum(mrrs) / len(mrrs), 4),
        "latency_p50_ms": round(lats_sorted[int(len(lats_sorted) * 0.50)], 2),
        "latency_p95_ms": round(lats_sorted[int(len(lats_sorted) * 0.95)], 2),
        "n_queries": len(ndcgs),
    }


# ---------------------------------------------------------------------------
# Elasticsearch benchmark
# ---------------------------------------------------------------------------

def run_elasticsearch(es_url: str, docs: list[dict], queries: list[dict],
                      index_name: str, dataset_label: str) -> dict:
    from elasticsearch import Elasticsearch, NotFoundError

    print(f"\n[ES] {dataset_label}: connecting to {es_url}...")
    es = Elasticsearch(es_url, request_timeout=60)

    # Wait for ES to be ready
    for _ in range(30):
        try:
            health = es.cluster.health(wait_for_status="yellow", timeout="5s")
            print(f"  cluster status: {health['status']}")
            break
        except Exception as e:
            print(f"  waiting for ES... ({e})")
            time.sleep(3)

    # Create index
    try:
        es.indices.delete(index=index_name)
    except NotFoundError:
        pass

    es.indices.create(index=index_name, body={
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
            "analysis": {
                "analyzer": {
                    "korean": {
                        "type": "nori",
                        "decompound_mode": "mixed",
                    }
                }
            }
        },
        "mappings": {
            "properties": {
                "doc_id": {"type": "keyword"},
                "text": {"type": "text", "analyzer": "korean"},
            }
        }
    })

    # Index documents in bulk
    print(f"  indexing {len(docs)} documents...")
    from elasticsearch.helpers import bulk

    def gen():
        for doc in docs:
            yield {
                "_index": index_name,
                "_id": str(doc["id"]),
                "_source": {"doc_id": str(doc["id"]), "text": doc["text"]},
            }

    t0 = time.time()
    ok, errors = bulk(es, gen(), chunk_size=500, request_timeout=120)
    es.indices.refresh(index=index_name)
    build_sec = round(time.time() - t0, 1)
    print(f"  indexed {ok} docs in {build_sec}s, errors={len(errors) if errors else 0}")

    # Search function
    def search_es(text: str) -> list[str]:
        resp = es.search(index=index_name, body={
            "query": {"match": {"text": {"query": text, "analyzer": "korean"}}},
            "size": 10,
            "_source": False,
        })
        return [hit["_id"] for hit in resp["hits"]["hits"]]

    # Evaluate
    print(f"  evaluating {len(queries)} queries...")
    metrics = evaluate(search_es, queries)
    metrics["index_build_sec"] = build_sec
    metrics["n_docs"] = len(docs)
    print(f"  NDCG@10={metrics['ndcg_at_10']} R@10={metrics['recall_at_10']} "
          f"MRR={metrics['mrr']} p50={metrics['latency_p50_ms']}ms")
    return metrics


# ---------------------------------------------------------------------------
# Qdrant benchmark
# ---------------------------------------------------------------------------

def run_qdrant(qdrant_url: str, docs: list[dict], queries: list[dict],
               collection_name: str, dataset_label: str) -> dict:
    from qdrant_client import QdrantClient
    from qdrant_client.models import (
        SparseVectorParams, SparseVector, NamedSparseVector,
        VectorParams, Distance, SparseIndexParams,
    )
    from src.bm25_module import BM25Embedder_PG

    print(f"\n[Qdrant] {dataset_label}: connecting to {qdrant_url}...")
    host, port_str = qdrant_url.replace("http://", "").split(":")
    client = QdrantClient(host=host, port=int(port_str))

    # Fit BM25
    print(f"  fitting BM25 (kiwi-cong) on {len(docs)} docs...")
    texts = [d["text"] for d in docs]
    ids   = [str(d["id"]) for d in docs]
    emb = BM25Embedder_PG(tokenizer="kiwi-cong")
    t0 = time.time()
    emb.fit(texts)
    fit_sec = round(time.time() - t0, 1)
    print(f"  fit done in {fit_sec}s, vocab={emb.vocab_size}")

    # Recreate collection
    try:
        client.delete_collection(collection_name)
    except Exception:
        pass

    client.create_collection(
        collection_name=collection_name,
        vectors_config={},  # no dense vectors
        sparse_vectors_config={
            "bm25": SparseVectorParams(
                index=SparseIndexParams(on_disk=False),
            )
        }
    )

    # Embed and upload
    print(f"  embedding + uploading {len(docs)} documents...")
    t0 = time.time()
    from qdrant_client.models import PointStruct

    batch_size = 500
    for i in range(0, len(docs), batch_size):
        batch_docs = docs[i:i + batch_size]
        batch_ids  = ids[i:i + batch_size]
        points = []
        for doc, doc_id in zip(batch_docs, batch_ids):
            sv = emb.embed_document(doc["text"])
            # sv is a SparseVector-like object with indices/values
            # Convert to qdrant SparseVector
            sv_dict = sv.to_dict() if hasattr(sv, "to_dict") else {}
            if not sv_dict:
                # Try different access patterns
                try:
                    sv_dict = {int(k): float(v) for k, v in sv.items()}
                except Exception:
                    indices = list(sv.indices) if hasattr(sv, "indices") else []
                    values  = list(sv.values)  if hasattr(sv, "values")  else []
                    sv_dict = {int(k): float(v) for k, v in zip(indices, values)}

            points.append(PointStruct(
                id=abs(hash(doc_id)) % (2**63),  # Qdrant needs uint64
                payload={"doc_id": doc_id},
                vector={"bm25": SparseVector(
                    indices=list(sv_dict.keys()),
                    values=list(sv_dict.values()),
                )},
            ))
        client.upsert(collection_name=collection_name, points=points)
        print(f"  {min(i + batch_size, len(docs))}/{len(docs)}", end="\r")

    build_sec = round(time.time() - t0, 1)
    print(f"\n  uploaded in {build_sec}s")

    # Build query ID map: hash -> doc_id string (for result mapping)
    id_map = {abs(hash(doc_id)) % (2**63): doc_id for doc_id in ids}

    # Search function
    def search_qdrant(text: str) -> list[str]:
        qv = emb.embed_query(text)
        try:
            qv_dict = {int(k): float(v) for k, v in qv.items()}
        except Exception:
            indices = list(qv.indices) if hasattr(qv, "indices") else []
            values  = list(qv.values)  if hasattr(qv, "values")  else []
            qv_dict = {int(k): float(v) for k, v in zip(indices, values)}

        results = client.search(
            collection_name=collection_name,
            query_vector=NamedSparseVector(
                name="bm25",
                vector=SparseVector(
                    indices=list(qv_dict.keys()),
                    values=list(qv_dict.values()),
                ),
            ),
            limit=10,
            with_payload=True,
        )
        return [r.payload["doc_id"] for r in results]

    # Evaluate
    print(f"  evaluating {len(queries)} queries...")
    metrics = evaluate(search_qdrant, queries)
    metrics["index_build_sec"] = build_sec + fit_sec
    metrics["n_docs"] = len(docs)
    print(f"  NDCG@10={metrics['ndcg_at_10']} R@10={metrics['recall_at_10']} "
          f"MRR={metrics['mrr']} p50={metrics['latency_p50_ms']}ms")
    return metrics


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--es-url", default="http://localhost:9200")
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--output-dir", default="results/phase5")
    parser.add_argument("--skip-es", action="store_true")
    parser.add_argument("--skip-qdrant", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load data
    print("[Data] loading MIRACL + EZIS...")
    miracl_docs    = json.loads(Path("data/miracl/docs_ko_miracl.json").read_text())
    miracl_queries = json.loads(Path("data/miracl/queries_dev.json").read_text())
    ezis_chunks    = json.loads(Path("data/ezis/chunks.json").read_text())
    ezis_queries   = json.loads(Path("data/ezis/queries.json").read_text())

    # EZIS docs have different key names
    ezis_docs = [{"id": c["id"], "text": c["text"]} for c in ezis_chunks]

    print(f"  MIRACL: {len(miracl_docs)} docs, {len(miracl_queries)} queries")
    print(f"  EZIS:   {len(ezis_docs)} docs, {len(ezis_queries)} queries")

    results = {}

    # -----------------------------------------------------------------------
    # Elasticsearch
    # -----------------------------------------------------------------------
    if not args.skip_es:
        results["es_miracl"] = run_elasticsearch(
            args.es_url, miracl_docs, miracl_queries,
            index_name="docs_ko_miracl", dataset_label="MIRACL"
        )
        results["es_ezis"] = run_elasticsearch(
            args.es_url, ezis_docs, ezis_queries,
            index_name="docs_ko_ezis", dataset_label="EZIS"
        )

    # -----------------------------------------------------------------------
    # Qdrant
    # -----------------------------------------------------------------------
    if not args.skip_qdrant:
        results["qdrant_miracl"] = run_qdrant(
            args.qdrant_url, miracl_docs, miracl_queries,
            collection_name="docs_ko_miracl", dataset_label="MIRACL"
        )
        results["qdrant_ezis"] = run_qdrant(
            args.qdrant_url, ezis_docs, ezis_queries,
            collection_name="docs_ko_ezis", dataset_label="EZIS"
        )

    # -----------------------------------------------------------------------
    # Save results
    # -----------------------------------------------------------------------
    out_path = out_dir / "phase5_es_qdrant.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"\n[Done] results saved to {out_path}")

    # Print comparison table
    print("\n=== Phase 5: System Comparison ===")
    print(f"{'System':<35} {'MIRACL NDCG@10':>14} {'MIRACL p50':>12} {'EZIS NDCG@10':>14}")
    print("-" * 78)

    # PostgreSQL baselines (from phase results)
    pg_baselines = [
        ("PG pl/pgsql BM25+MeCab (phase2)",   0.6412, "10ms",  0.9290),
        ("PG pgvector-sparse kiwi-cong (p3)",  0.6326, "4ms",   0.9455),
        ("PG BGE-M3 dense (phase4)",           0.7915, "253ms", 0.8060),
    ]
    for name, mn, ml, en in pg_baselines:
        print(f"  {name:<33} {mn:>14.4f} {ml:>12} {en:>14.4f}")

    print()
    if "es_miracl" in results and "es_ezis" in results:
        em = results["es_miracl"]
        ee = results["es_ezis"]
        print(f"  {'ES nori BM25':<33} {em['ndcg_at_10']:>14.4f} {em['latency_p50_ms']:>10.1f}ms {ee['ndcg_at_10']:>14.4f}")

    if "qdrant_miracl" in results and "qdrant_ezis" in results:
        qm = results["qdrant_miracl"]
        qe = results["qdrant_ezis"]
        print(f"  {'Qdrant sparse BM25 kiwi-cong':<33} {qm['ndcg_at_10']:>14.4f} {qm['latency_p50_ms']:>10.1f}ms {qe['ndcg_at_10']:>14.4f}")


if __name__ == "__main__":
    main()
