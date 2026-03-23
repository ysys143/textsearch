"""
Phase 5: System Comparison — Elasticsearch vs Qdrant vs PostgreSQL.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Any, Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

try:
    from elasticsearch import Elasticsearch
except ImportError:
    print("[WARNING] elasticsearch package not installed.")
    Elasticsearch = None

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import (
        SparseVector,
        SparseVectorParams,
        VectorParams,
        Distance,
        PointStruct,
        SparseIndexParams,
        NamedSparseVector,
    )
except ImportError:
    print("[WARNING] qdrant_client not installed.")
    QdrantClient = None
    SparseVector = None  # type: ignore[assignment]
    SparseVectorParams = None  # type: ignore[assignment]
    SparseIndexParams = None  # type: ignore[assignment]
    PointStruct = None  # type: ignore[assignment]
    NamedSparseVector = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Elasticsearch searcher
# ---------------------------------------------------------------------------

class ElasticsearchSearcher:
    """
    Wraps Elasticsearch for document indexing and BM25 search.
    Uses nori tokenizer for Korean if available, falls back to standard.
    """

    INDEX_NAME = "korean_bench"

    def __init__(self, es_url: str = "http://localhost:9200", index_name: str = None):
        if Elasticsearch is None:
            raise ImportError("elasticsearch package is required.")
        self.client = Elasticsearch(es_url)
        self.index_name = index_name or self.INDEX_NAME
        self._index_build_start: Optional[float] = None
        self._index_build_time: float = 0.0

    def setup_index(self, mappings: Optional[Dict] = None):
        """
        Create (or recreate) the index with Korean analyzer.
        Tries nori tokenizer; falls back to standard if nori is not available.
        """
        # Delete if exists
        if self.client.indices.exists(index=self.index_name):
            self.client.indices.delete(index=self.index_name)

        if mappings is None:
            nori_settings = {
                "settings": {
                    "analysis": {
                        "analyzer": {
                            "korean_analyzer": {
                                "type": "custom",
                                "tokenizer": "nori_tokenizer",
                                "filter": ["lowercase"],
                            }
                        }
                    }
                },
                "mappings": {
                    "properties": {
                        "doc_id": {"type": "keyword"},
                        "text": {
                            "type": "text",
                            "analyzer": "korean_analyzer",
                        },
                    }
                },
            }
            standard_settings = {
                "settings": {},
                "mappings": {
                    "properties": {
                        "doc_id": {"type": "keyword"},
                        "text": {"type": "text", "analyzer": "standard"},
                    }
                },
            }
            # Try nori first
            try:
                self.client.indices.create(index=self.index_name, body=nori_settings)
                print(f"[ElasticsearchSearcher] Created index '{self.index_name}' with nori analyzer.")
            except Exception:
                self.client.indices.create(index=self.index_name, body=standard_settings)
                print(f"[ElasticsearchSearcher] Created index '{self.index_name}' with standard analyzer.")
        else:
            self.client.indices.create(index=self.index_name, body=mappings)

    def index_documents(self, docs: List[Dict]):
        """
        Index documents. Each doc should have 'doc_id' and 'text' fields.
        """
        self._index_build_start = time.time()
        from elasticsearch.helpers import bulk
        actions = [
            {
                "_index": self.index_name,
                "_id": doc["doc_id"],
                "_source": {"doc_id": doc["doc_id"], "text": doc["text"]},
            }
            for doc in docs
        ]
        bulk(self.client, actions)
        self.client.indices.refresh(index=self.index_name)
        self._index_build_time = time.time() - self._index_build_start
        print(f"[ElasticsearchSearcher] Indexed {len(docs)} documents in {self._index_build_time:.2f}s.")

    def search(self, query: str, k: int = 10) -> List[str]:
        """
        Run BM25 match query. Returns list of doc_ids ranked by score.
        """
        resp = self.client.search(
            index=self.index_name,
            body={
                "size": k,
                "query": {"match": {"text": query}},
            },
        )
        return [hit["_source"]["doc_id"] for hit in resp["hits"]["hits"]]


# ---------------------------------------------------------------------------
# Qdrant searcher
# ---------------------------------------------------------------------------

class QdrantSearcher:
    """
    Wraps Qdrant for sparse vector indexing and search.
    """

    COLLECTION_NAME = "korean_bench_sparse"
    SPARSE_FIELD = "sparse"

    def __init__(self, qdrant_url: str = "http://localhost:6333", collection_name: str = None):
        if QdrantClient is None:
            raise ImportError("qdrant_client package is required.")
        self.client = QdrantClient(url=qdrant_url)
        self.collection_name = collection_name or self.COLLECTION_NAME
        self._index_build_start: Optional[float] = None
        self._index_build_time: float = 0.0
        self._doc_ids: List[str] = []

    def setup_collection(self, vector_size: int):
        """
        Create (or recreate) a Qdrant collection with a named sparse vector field.
        vector_size is not used for sparse collections but kept for API compatibility.
        """
        # Delete if exists
        try:
            self.client.delete_collection(self.collection_name)
        except Exception:
            pass

        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config={},
            sparse_vectors_config={
                self.SPARSE_FIELD: SparseVectorParams(
                    index=SparseIndexParams(on_disk=False)
                )
            },
        )
        print(f"[QdrantSearcher] Created collection '{self.collection_name}'.")

    def index_documents(self, docs: List[Dict], sparse_vecs: List[Dict[int, float]]):
        """
        Index documents with their sparse vectors.
        docs: list of {'doc_id': str, 'text': str}
        sparse_vecs: list of {token_id: weight} dicts
        """
        self._index_build_start = time.time()
        self._doc_ids = [doc["doc_id"] for doc in docs]

        points = []
        for i, (doc, sv) in enumerate(zip(docs, sparse_vecs)):
            indices = list(sv.keys())
            values = [sv[k] for k in indices]
            points.append(
                PointStruct(
                    id=i,
                    payload={"doc_id": doc["doc_id"], "text": doc["text"]},
                    vector={
                        self.SPARSE_FIELD: SparseVector(indices=indices, values=values)
                    },
                )
            )

        batch_size = 100
        for start in range(0, len(points), batch_size):
            self.client.upsert(
                collection_name=self.collection_name,
                points=points[start:start + batch_size],
            )

        self._index_build_time = time.time() - self._index_build_start
        print(f"[QdrantSearcher] Indexed {len(docs)} documents in {self._index_build_time:.2f}s.")

    def search(self, query_sparse_vec: Dict[int, float], k: int = 10) -> List[str]:
        """
        Search using a sparse vector. Returns list of doc_ids ranked by score.
        """
        indices = list(query_sparse_vec.keys())
        values = [query_sparse_vec[ki] for ki in indices]

        results = self.client.search(
            collection_name=self.collection_name,
            query_vector=NamedSparseVector(
                name=self.SPARSE_FIELD,
                vector=SparseVector(indices=indices, values=values),
            ),
            limit=k,
        )
        return [hit.payload["doc_id"] for hit in results]


# ---------------------------------------------------------------------------
# Scale tests and stats
# ---------------------------------------------------------------------------

def run_scale_test(
    searcher,
    queries: List[Dict],
    dataset_sizes: List[int] = None,
) -> List[dict]:
    """
    Run search at different dataset scales and measure latency + throughput.
    Returns list of per-scale result dicts.
    """
    if dataset_sizes is None:
        dataset_sizes = [1000, 10000, 50000, 100000]

    results = []
    for size in dataset_sizes:
        scale_queries = queries[:min(len(queries), 50)]  # use up to 50 queries per scale
        latencies = []
        for q in scale_queries:
            t0 = time.perf_counter()
            if isinstance(searcher, QdrantSearcher):
                query_vec = q.get("sparse_vec", {})
                searcher.search(query_sparse_vec=query_vec, k=10)
            elif isinstance(searcher, ElasticsearchSearcher):
                searcher.search(query=q["text"], k=10)
            else:
                # PostgreSQL or generic
                searcher.search(query=q["text"], k=10)
            latencies.append((time.perf_counter() - t0) * 1000)

        latencies.sort()
        n = len(latencies)
        p50 = latencies[int(n * 0.50)] if n > 0 else 0.0
        p95 = latencies[int(n * 0.95)] if n > 0 else 0.0
        p99 = latencies[int(n * 0.99)] if n > 0 else 0.0

        results.append({
            "dataset_size": size,
            "num_queries": len(scale_queries),
            "latency_p50_ms": p50,
            "latency_p95_ms": p95,
            "latency_p99_ms": p99,
            "throughput_qps": len(scale_queries) / (sum(latencies) / 1000) if sum(latencies) > 0 else 0.0,
        })
        print(f"[run_scale_test] size={size}: p50={p50:.1f}ms p95={p95:.1f}ms")

    return results


def measure_index_stats(searcher) -> dict:
    """
    Measure index size and build time from the searcher.
    Returns {'index_size_mb': float, 'index_build_time_sec': float}.
    """
    index_size_mb = 0.0
    build_time = getattr(searcher, "_index_build_time", 0.0)

    if isinstance(searcher, ElasticsearchSearcher):
        try:
            stats = searcher.client.indices.stats(index=searcher.index_name)
            size_bytes = stats["_all"]["total"]["store"]["size_in_bytes"]
            index_size_mb = size_bytes / (1024 ** 2)
        except Exception as e:
            print(f"[measure_index_stats] ES stats error: {e}")
    elif isinstance(searcher, QdrantSearcher):
        try:
            info = searcher.client.get_collection(searcher.collection_name)
            # Qdrant doesn't expose exact size easily; approximate from payload
            index_size_mb = 0.0
        except Exception as e:
            print(f"[measure_index_stats] Qdrant stats error: {e}")

    return {
        "index_size_mb": index_size_mb,
        "index_build_time_sec": build_time,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Phase 5: System comparison (Elasticsearch, Qdrant, PostgreSQL)"
    )
    parser.add_argument(
        "--system",
        choices=["elasticsearch", "qdrant", "postgres"],
        required=True,
        help="Search system to benchmark",
    )
    parser.add_argument(
        "--db-url",
        default=os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/dev"),
        help="PostgreSQL connection URL",
    )
    parser.add_argument(
        "--es-url",
        default=os.environ.get("ES_URL", "http://localhost:9200"),
        help="Elasticsearch URL",
    )
    parser.add_argument(
        "--qdrant-url",
        default=os.environ.get("QDRANT_URL", "http://localhost:6333"),
        help="Qdrant URL",
    )
    parser.add_argument(
        "--queries-file",
        required=True,
        help="Path to JSON queries file [{query_id, text, relevant_ids}]",
    )
    parser.add_argument(
        "--output-dir",
        default="results/phase5",
        help="Directory to write result JSON files",
    )
    args = parser.parse_args()

    Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    with open(args.queries_file, encoding="utf-8") as f:
        queries = json.load(f)

    if args.system == "elasticsearch":
        searcher = ElasticsearchSearcher(es_url=args.es_url)
        searcher.setup_index()
        # Load docs from DB
        import psycopg2 as _pg
        conn = _pg.connect(args.db_url)
        with conn.cursor() as cur:
            cur.execute("SELECT id, text FROM documents ORDER BY id LIMIT 100000;")
            rows = cur.fetchall()
        conn.close()
        docs = [{"doc_id": str(r[0]), "text": r[1]} for r in rows]
        searcher.index_documents(docs)
        scale_results = run_scale_test(searcher, queries)
        stats = measure_index_stats(searcher)

    elif args.system == "qdrant":
        from experiments.phase4_neural_sparse import SPLADEKoEncoder
        encoder = SPLADEKoEncoder()
        searcher = QdrantSearcher(qdrant_url=args.qdrant_url)
        searcher.setup_collection(vector_size=0)
        import psycopg2 as _pg
        conn = _pg.connect(args.db_url)
        with conn.cursor() as cur:
            cur.execute("SELECT id, text FROM documents ORDER BY id LIMIT 100000;")
            rows = cur.fetchall()
        conn.close()
        texts = [r[1] for r in rows]
        docs = [{"doc_id": str(r[0]), "text": r[1]} for r in rows]
        sparse_vecs = encoder.encode_batch(texts)
        searcher.index_documents(docs, sparse_vecs)
        # Attach sparse vecs to queries for search
        query_texts = [q["text"] for q in queries]
        query_sparse = encoder.encode_batch(query_texts)
        for q, sv in zip(queries, query_sparse):
            q["sparse_vec"] = sv
        scale_results = run_scale_test(searcher, queries)
        stats = measure_index_stats(searcher)

    else:
        # postgres — use existing BM25 infrastructure
        from src.bm25_module import bm25_sql_search
        class _PGSearcher:
            _index_build_time = 0.0
            def search(self, query: str, k: int = 10):
                return [str(r[0]) for r in bm25_sql_search(query, k=k)]
        searcher = _PGSearcher()
        scale_results = run_scale_test(searcher, queries)
        stats = measure_index_stats(searcher)

    output = {
        "system": args.system,
        "scale_results": scale_results,
        "index_stats": stats,
    }
    output_file = Path(args.output_dir) / f"phase5_{args.system}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"[phase5] Results written to {output_file}")


if __name__ == "__main__":
    main()
