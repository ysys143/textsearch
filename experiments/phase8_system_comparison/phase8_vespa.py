"""
Phase 8: Vespa Benchmark

Infrastructure: Vespa (vespaengine/vespa:latest)
  - BM25 text index
  - HNSW dense vectors (BGE-M3 1024-dim, angular distance)
  - Hybrid: BM25 + nearestNeighbor with linear score combination

Tokenizer modes:
  --tokenizer icu   : Default ICU word boundary segmentation (non-morphological)
  --tokenizer nori  : Lucene Linguistics + Nori analyzer (morphological, same as ES nori)
                      Requires Vespa >= 8.315.19 (lucene-linguistics bundle built-in)

Methods:
  1. BM25    : Vespa BM25 rank profile, userQuery()
  2. Dense   : nearestNeighbor ANN (BGE-M3, retrieval-only*)
  3. Hybrid  : BM25 + closeness linear combination (0.1*bm25 + closeness)

*retrieval-only: BGE-M3 inference excluded

Datasets: MIRACL-ko 10K (213 queries), EZIS 97 (131 queries)
Metrics:  NDCG@10, Recall@10, MRR, latency p50/p95/p99

Usage:
  # Start Vespa first:
  #   docker compose --profile phase8-vespa up -d
  #   # Wait ~60s for Vespa to initialize

  # ICU tokenizer (default, non-morphological):
  uv run python3 experiments/phase8_system_comparison/phase8_vespa.py \\
    --vespa-url http://localhost:8090 \\
    --config-url http://localhost:19071 \\
    --output-dir results/phase8

  # Nori tokenizer (morphological, Lucene Linguistics):
  uv run python3 experiments/phase8_system_comparison/phase8_vespa.py \\
    --tokenizer nori \\
    --vespa-url http://localhost:8090 \\
    --config-url http://localhost:19071 \\
    --output-dir results/phase8
"""

import argparse
import io
import json
import math
import os
import time
import zipfile
from datetime import datetime
from typing import Dict, List, Optional

import requests

MIRACL_QUERIES_PATH = "data/miracl/queries_dev.json"
EZIS_QUERIES_PATH   = "data/ezis/queries.json"
EMB_MIRACL_PATH     = "data/phase8/query_embs_miracl.json"
EMB_EZIS_PATH       = "data/phase8/query_embs_ezis.json"
DOC_EMB_MIRACL_PATH = "data/phase8/doc_embs_miracl.json"
DOC_EMB_EZIS_PATH   = "data/phase8/doc_embs_ezis.json"
N_MIRACL = 213
TOPK     = 60
WARMUP   = 5


SERVICES_XML_ICU = """\
<?xml version="1.0" encoding="utf-8" ?>
<services version="1.0">
  <container id="default" version="1.0">
    <search />
    <document-api />
  </container>
  <content id="content" version="1.0">
    <redundancy>1</redundancy>
    <documents>
      <document type="doc" mode="index" />
    </documents>
    <nodes>
      <node distribution-key="0" hostalias="node1" />
    </nodes>
  </content>
</services>
"""

SERVICES_XML_NORI = """\
<?xml version="1.0" encoding="utf-8" ?>
<services version="1.0" minimum-required-vespa-version="8.315.19">
  <container id="default" version="1.0">
    <component id="linguistics"
               class="com.yahoo.language.lucene.LuceneLinguistics"
               bundle="lucene-linguistics">
      <config name="com.yahoo.language.lucene.lucene-analysis"/>
    </component>
    <search />
    <document-api />
    <document-processing />
  </container>
  <content id="content" version="1.0">
    <redundancy>1</redundancy>
    <documents>
      <document type="doc" mode="index" />
    </documents>
    <nodes>
      <node distribution-key="0" hostalias="node1" />
    </nodes>
  </content>
</services>
"""

DOC_SD_ICU = """\
schema doc {
  document doc {
    field doc_id type string {
      indexing: attribute | summary
    }
    field text type string {
      indexing: index | summary
      index: enable-bm25
    }
    field dense_vec type tensor<float>(x[1024]) {
      indexing: attribute | index
      attribute {
        distance-metric: angular
      }
      index {
        hnsw {
          max-links-per-node: 16
          neighbors-to-explore-at-insert: 200
        }
      }
    }
  }

  rank-profile bm25_rank inherits default {
    first-phase {
      expression: bm25(text)
    }
  }

  rank-profile dense_rank inherits default {
    inputs {
      query(q_dense) tensor<float>(x[1024])
    }
    first-phase {
      expression: closeness(field, dense_vec)
    }
  }

  rank-profile hybrid_rank inherits default {
    inputs {
      query(q_dense) tensor<float>(x[1024])
    }
    first-phase {
      expression: 0.1 * bm25(text) + closeness(field, dense_vec)
    }
  }
}
"""

DOC_SD_NORI = """\
schema doc {
  document doc {
    field doc_id type string {
      indexing: attribute | summary
    }
    field text type string {
      indexing: index | summary
      index: enable-bm25
    }
    field dense_vec type tensor<float>(x[1024]) {
      indexing: attribute | index
      attribute {
        distance-metric: angular
      }
      index {
        hnsw {
          max-links-per-node: 16
          neighbors-to-explore-at-insert: 200
        }
      }
    }
  }

  rank-profile bm25_rank inherits default {
    first-phase {
      expression: bm25(text)
    }
  }

  rank-profile dense_rank inherits default {
    inputs {
      query(q_dense) tensor<float>(x[1024])
    }
    first-phase {
      expression: closeness(field, dense_vec)
    }
  }

  rank-profile hybrid_rank inherits default {
    inputs {
      query(q_dense) tensor<float>(x[1024])
    }
    first-phase {
      expression: 0.1 * bm25(text) + closeness(field, dense_vec)
    }
  }
}
"""

HOSTS_XML = """\
<?xml version="1.0" encoding="utf-8" ?>
<hosts>
  <host name="localhost">
    <alias>node1</alias>
  </host>
</hosts>
"""


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def ndcg_at_k(relevant_ids: List[str], retrieved_ids: List[str], k: int = 10) -> float:
    relevant_set = set(relevant_ids)
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, doc_id in enumerate(retrieved_ids[:k], start=1)
        if doc_id in relevant_set
    )
    ideal_hits = min(len(relevant_set), k)
    idcg = sum(1.0 / math.log2(r + 1) for r in range(1, ideal_hits + 1))
    return dcg / idcg if idcg > 0 else 0.0


def recall_at_k(relevant_ids: List[str], retrieved_ids: List[str], k: int = 10) -> float:
    relevant_set = set(relevant_ids)
    hits = sum(1 for doc_id in retrieved_ids[:k] if doc_id in relevant_set)
    return hits / len(relevant_set) if relevant_set else 0.0


def mrr_at_k(relevant_ids: List[str], retrieved_ids: List[str], k: int = 10) -> float:
    relevant_set = set(relevant_ids)
    for rank, doc_id in enumerate(retrieved_ids[:k], start=1):
        if doc_id in relevant_set:
            return 1.0 / rank
    return 0.0


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_queries(path: str, limit: Optional[int] = None) -> List[dict]:
    with open(path, encoding="utf-8") as f:
        qs = json.load(f)
    return qs[:limit] if limit else qs


def load_embeddings(path: str) -> Dict[str, List[float]]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Application package deployment
# ---------------------------------------------------------------------------

def build_app_zip(tokenizer: str = "icu") -> bytes:
    services_xml = SERVICES_XML_NORI if tokenizer == "nori" else SERVICES_XML_ICU
    doc_sd = DOC_SD_NORI if tokenizer == "nori" else DOC_SD_ICU
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("services.xml", services_xml)
        zf.writestr("hosts.xml", HOSTS_XML)
        zf.writestr("schemas/doc.sd", doc_sd)
    return buf.getvalue()


def deploy_application(config_url: str, tokenizer: str = "icu") -> bool:
    app_zip = build_app_zip(tokenizer)
    resp = requests.post(
        f"{config_url}/application/v2/tenant/default/prepareandactivate",
        data=app_zip,
        headers={"Content-Type": "application/zip"},
        timeout=120,
    )
    if resp.status_code not in (200, 201):
        print(f"  [ERROR] Deploy failed: {resp.status_code} {resp.text[:200]}")
        return False
    print("  Application deployed successfully")
    time.sleep(5)  # Wait for config propagation
    return True


def wait_for_ready(vespa_url: str, timeout: int = 120) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = requests.get(f"{vespa_url}/ApplicationStatus", timeout=5)
            if resp.status_code in (200, 404):
                # 404 = container up but no app deployed yet — ready to deploy
                return True
        except Exception:
            pass
        time.sleep(3)
    return False


# ---------------------------------------------------------------------------
# Document feeding
# ---------------------------------------------------------------------------

def feed_documents(vespa_url: str, docs: List[dict],
                   embeddings: Dict[str, List[float]],
                   language: Optional[str] = None) -> float:
    t0 = time.perf_counter()
    for doc in docs:
        dense_emb = embeddings.get(doc["id"], [0.0] * 1024)
        fields = {
            "doc_id": doc["id"],
            "text": doc["text"],
            "dense_vec": {"values": dense_emb},
        }
        if language:
            fields["language"] = language
        body = {"fields": fields}
        doc_id_encoded = doc["id"].replace("/", "_").replace(":", "_")
        resp = requests.post(
            f"{vespa_url}/document/v1/doc/doc/docid/{doc_id_encoded}",
            json=body,
            timeout=30,
        )
        if resp.status_code not in (200, 201):
            print(f"  [WARN] Feed error for {doc['id']}: {resp.status_code}")

    # Trigger flush / wait for indexing
    time.sleep(3)
    return round(time.perf_counter() - t0, 2)


# ---------------------------------------------------------------------------
# Search functions
# ---------------------------------------------------------------------------

def search_bm25(vespa_url: str, query_text: str, k: int = 10,
                language: Optional[str] = None) -> List[str]:
    body = {
        "yql": f"select doc_id from doc where userQuery() limit {k}",
        "query": query_text,
        "type": "weakAnd",
        "model.defaultIndex": "text",
        "ranking": "bm25_rank",
        "hits": k,
    }
    if language:
        body["language"] = language
    resp = requests.post(f"{vespa_url}/search/", json=body, timeout=30)
    if resp.status_code != 200:
        return []
    hits = resp.json().get("root", {}).get("children", [])
    return [h["fields"]["doc_id"] for h in hits if "fields" in h]


def search_dense(vespa_url: str, query_emb: List[float], k: int = 10) -> List[str]:
    body = {
        "yql": (f"select doc_id from doc where "
                f"{{targetHits:{k}}}nearestNeighbor(dense_vec, q_dense) limit {k}"),
        "ranking": "dense_rank",
        "hits": k,
        "input.query(q_dense)": query_emb,
    }
    resp = requests.post(f"{vespa_url}/search/", json=body, timeout=30)
    if resp.status_code != 200:
        return []
    hits = resp.json().get("root", {}).get("children", [])
    return [h["fields"]["doc_id"] for h in hits if "fields" in h]


def search_hybrid(vespa_url: str, query_text: str,
                  query_emb: List[float], k: int = 10,
                  language: Optional[str] = None) -> List[str]:
    body = {
        "yql": (f"select doc_id from doc where "
                f"{{targetHits:{TOPK}}}nearestNeighbor(dense_vec, q_dense) or "
                f"userQuery() limit {k}"),
        "query": query_text,
        "type": "weakAnd",
        "model.defaultIndex": "text",
        "ranking": "hybrid_rank",
        "hits": k,
        "input.query(q_dense)": query_emb,
    }
    if language:
        body["language"] = language
    resp = requests.post(f"{vespa_url}/search/", json=body, timeout=30)
    if resp.status_code != 200:
        return []
    hits = resp.json().get("root", {}).get("children", [])
    return [h["fields"]["doc_id"] for h in hits if "fields" in h]


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

def bench_dataset(vespa_url: str, dataset_name: str,
                  queries: List[dict], query_embs: Dict[str, List[float]],
                  language: Optional[str] = None) -> List[dict]:
    print(f"\n  === {dataset_name} | {len(queries)} queries ===")

    methods = [
        ("BM25",   lambda t, e: search_bm25(vespa_url, t, language=language)),
        ("Dense",  lambda t, e: search_dense(vespa_url, e) if e else []),
        ("Hybrid", lambda t, e: search_hybrid(vespa_url, t, e, language=language) if e else []),
    ]

    results = []
    for name, fn in methods:
        ndcg_s, rec_s, mrr_s, lats = [], [], [], []

        for q in queries[:WARMUP]:
            fn(q["text"], query_embs.get(q["query_id"]))

        for q in queries:
            emb = query_embs.get(q["query_id"])
            t0 = time.perf_counter()
            retrieved = fn(q["text"], emb)
            lats.append((time.perf_counter() - t0) * 1000)

            rel = q.get("relevant_ids", [])
            if rel:
                ndcg_s.append(ndcg_at_k(rel, retrieved))
                rec_s.append(recall_at_k(rel, retrieved))
                mrr_s.append(mrr_at_k(rel, retrieved))

        lats.sort()
        n = len(lats)
        nq = len(ndcg_s)

        r = {
            "system": "vespa",
            "dataset": dataset_name,
            "method": name,
            "n_queries": len(queries),
            "ndcg_at_10":   round(sum(ndcg_s) / nq, 4) if nq else None,
            "recall_at_10": round(sum(rec_s) / nq, 4)  if nq else None,
            "mrr":          round(sum(mrr_s) / nq, 4)  if nq else None,
            "latency_p50":  round(lats[n // 2], 2),
            "latency_p95":  round(lats[int(n * 0.95)], 2),
            "latency_p99":  round(lats[int(n * 0.99)], 2),
        }
        print(f"  [{name:8s}] NDCG@10={r['ndcg_at_10']}  "
              f"Recall={r['recall_at_10']}  MRR={r['mrr']}  "
              f"p50={r['latency_p50']}ms")
        results.append(r)

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--vespa-url", default="http://localhost:8090")
    parser.add_argument("--config-url", default="http://localhost:19071")
    parser.add_argument("--output-dir", default="results/phase8")
    parser.add_argument("--tokenizer", choices=["icu", "nori"], default="icu",
                        help="Tokenizer: icu (default, non-morphological) or nori (Lucene Linguistics, morphological)")
    parser.add_argument("--skip-deploy", action="store_true",
                        help="Skip application deployment (reuse existing)")
    args = parser.parse_args()

    tokenizer = args.tokenizer
    language = "ko" if tokenizer == "nori" else None

    print("=" * 60)
    print(f"Phase 8: Vespa Benchmark (tokenizer={tokenizer})")
    print("=" * 60)
    if tokenizer == "nori":
        print("  [NOTE] Korean tokenizer: Lucene Nori (morphological)")
        print("  [NOTE] Requires Vespa >= 8.315.19 (lucene-linguistics bundle)")
    else:
        print("  [NOTE] Korean tokenizer: ICU (non-morphological)")
        print("  [NOTE] BM25 quality expected lower than nori/MeCab")

    # Deploy application
    if not args.skip_deploy:
        print(f"\n--- Deploying Vespa application (tokenizer={tokenizer}) ---")
        if not wait_for_ready(args.vespa_url, timeout=120):
            print("  [ERROR] Vespa not ready. Start with:")
            print("    docker compose --profile phase8-vespa up -d")
            return
        if not deploy_application(args.config_url, tokenizer):
            return

    # Load query embeddings
    miracl_queries = load_queries(MIRACL_QUERIES_PATH, limit=N_MIRACL)
    ezis_queries   = load_queries(EZIS_QUERIES_PATH)
    miracl_q_embs  = load_embeddings(EMB_MIRACL_PATH)
    ezis_q_embs    = load_embeddings(EMB_EZIS_PATH)

    # Load document embeddings
    miracl_d_embs  = load_embeddings(DOC_EMB_MIRACL_PATH)
    ezis_d_embs    = load_embeddings(DOC_EMB_EZIS_PATH)

    miracl_corpus_path = "data/miracl/corpus_10k.json"
    miracl_docs = []
    if os.path.exists(miracl_corpus_path):
        with open(miracl_corpus_path, encoding="utf-8") as f:
            miracl_docs = json.load(f)
    print(f"\n  MIRACL corpus: {len(miracl_docs)} docs, {len(miracl_queries)} queries")

    ezis_corpus_path = "data/ezis/corpus.json"
    ezis_docs = []
    if os.path.exists(ezis_corpus_path):
        with open(ezis_corpus_path, encoding="utf-8") as f:
            ezis_docs = json.load(f)
    print(f"  EZIS corpus: {len(ezis_docs)} docs, {len(ezis_queries)} queries")

    all_results = []

    # MIRACL
    if miracl_docs:
        print("\n--- Feeding MIRACL documents ---")
        t_feed = feed_documents(args.vespa_url, miracl_docs, miracl_d_embs, language=language)
        print(f"  Fed {len(miracl_docs)} docs in {t_feed}s")
        all_results += bench_dataset(args.vespa_url, "MIRACL",
                                     miracl_queries, miracl_q_embs,
                                     language=language)

    # EZIS — requires re-deploying or using separate schema namespace
    # For simplicity, reuse same schema and re-feed (small corpus)
    if ezis_docs:
        print("\n--- Re-feeding with EZIS documents (replaces MIRACL) ---")
        # Delete all docs first
        requests.delete(
            f"{args.vespa_url}/document/v1/doc/doc/docid/",
            params={"selection": "true", "cluster": "content"},
            timeout=60,
        )
        time.sleep(2)
        t_feed = feed_documents(args.vespa_url, ezis_docs, ezis_d_embs, language=language)
        print(f"  Fed {len(ezis_docs)} docs in {t_feed}s")
        all_results += bench_dataset(args.vespa_url, "EZIS",
                                     ezis_queries, ezis_q_embs,
                                     language=language)

    os.makedirs(args.output_dir, exist_ok=True)
    suffix = f"_{'nori' if tokenizer == 'nori' else 'icu'}"
    json_path = os.path.join(args.output_dir, f"phase8_vespa{suffix}.json")
    tokenizer_desc = {
        "icu": "icu_default (non-morphological)",
        "nori": "lucene_nori (morphological, Lucene Linguistics)",
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "generated": datetime.now().isoformat(),
            "system": "vespa",
            "tokenizer": tokenizer_desc[tokenizer],
            "results": all_results,
        }, f, indent=2, ensure_ascii=False)
    print(f"\n  JSON: {json_path}")
    print("Done.")


if __name__ == "__main__":
    main()
