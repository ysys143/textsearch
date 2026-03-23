"""
Phase 4: Neural Sparse Retrieval — SPLADE-Ko and BGE-M3 sparse encoders.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Any

try:
    import torch
except ImportError:
    print("[WARNING] torch not installed.")
    torch = None  # type: ignore[assignment]

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

try:
    from transformers import AutoTokenizer, AutoModelForMaskedLM
except ImportError:
    print("[WARNING] transformers not installed.")
    AutoTokenizer = None
    AutoModelForMaskedLM = None

try:
    import psycopg2
    from pgvector.psycopg2 import register_vector, SparseVector
except ImportError:
    print("[WARNING] psycopg2 or pgvector not installed.")
    psycopg2 = None
    register_vector = None
    SparseVector = None


# ---------------------------------------------------------------------------
# SPLADE-Ko encoder
# ---------------------------------------------------------------------------

class SPLADEKoEncoder:
    """
    SPLADE sparse encoder using yjoonjang/splade-ko-v1.
    Produces sparse vectors via ReLU + log(1+x) activation over MLM logits.
    """

    MODEL_NAME = "yjoonjang/splade-ko-v1"

    def __init__(self, model_name: str = None, device: str = "cpu"):
        self.model_name = model_name or self.MODEL_NAME
        self.device = device
        self.tokenizer = None
        self.model = None

    def _load(self):
        if self.model is not None:
            return
        if AutoTokenizer is None or AutoModelForMaskedLM is None:
            raise RuntimeError("transformers package is required: pip install transformers")
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        self.model = AutoModelForMaskedLM.from_pretrained(self.model_name)
        self.model.to(self.device)
        self.model.eval()

    def encode_batch(self, texts: List[str]) -> List[Dict[int, float]]:
        """
        Encode a batch of texts into sparse vectors.
        Returns a list of {token_id: weight} dicts.
        """
        self._load()
        assert torch is not None, "torch required: pip install torch"
        assert self.tokenizer is not None and self.model is not None
        results = []
        with torch.no_grad():
            for text in texts:
                inputs = self.tokenizer(
                    text,
                    return_tensors="pt",
                    truncation=True,
                    max_length=512,
                    padding=True,
                ).to(self.device)
                outputs = self.model(**inputs)
                # logits: (1, seq_len, vocab_size)
                logits = outputs.logits
                # Apply ReLU + log(1+x), then max over sequence dimension
                activated = torch.log(1 + torch.relu(logits))  # (1, seq_len, vocab_size)
                # attention_mask: (1, seq_len) — mask out padding
                attention_mask = inputs["attention_mask"].unsqueeze(-1).float()
                activated = activated * attention_mask
                sparse_vec, _ = activated.max(dim=1)  # (1, vocab_size)
                sparse_vec = sparse_vec.squeeze(0).cpu()
                # Convert to dict of non-zero entries
                nonzero_indices = sparse_vec.nonzero(as_tuple=True)[0].tolist()
                nonzero_values = sparse_vec[nonzero_indices].tolist()
                results.append({int(idx): float(val) for idx, val in zip(nonzero_indices, nonzero_values)})
        return results

    def model_size_mb(self) -> float:
        self._load()
        param_size = sum(p.numel() * p.element_size() for p in self.model.parameters())
        return param_size / (1024 ** 2)


# ---------------------------------------------------------------------------
# BGE-M3 sparse encoder
# ---------------------------------------------------------------------------

class BGEM3SparseEncoder:
    """
    BGE-M3 sparse encoder using BAAI/bge-m3 via sentence_transformers.
    """

    MODEL_NAME = "BAAI/bge-m3"

    def __init__(self, model_name: str | None = None, device: str = "cpu"):
        self.model_name = model_name or self.MODEL_NAME
        self.device = device
        self._model = None

    def _load(self):
        if self._model is not None:
            return
        try:
            # Stub broken minicpm reranker module (incompatible with transformers>=5)
            import types, sys
            _stub = types.ModuleType(
                "FlagEmbedding.inference.reranker.decoder_only.models.modeling_minicpm_reranker"
            )
            _stub.LayerWiseMiniCPMForCausalLM = None
            sys.modules[
                "FlagEmbedding.inference.reranker.decoder_only.models.modeling_minicpm_reranker"
            ] = _stub
            from FlagEmbedding import BGEM3FlagModel
            self._model = BGEM3FlagModel(self.model_name, use_fp16=(self.device == "cuda"))
            self._backend = "flagembedding"
        except ImportError:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name, device=self.device)
            self._backend = "sentence_transformers"

    def encode_batch(self, texts: List[str]) -> List[Dict[int, float]]:
        """
        Encode a batch of texts into sparse vectors.
        Returns a list of {token_id: weight} dicts.
        """
        self._load()
        results = []
        if self._backend == "flagembedding":
            output = self._model.encode(
                texts,
                return_sparse=True,
                return_dense=False,
                return_colbert_vecs=False,
            )
            sparse_list = output.get("lexical_weights", [])
            for sv in sparse_list:
                results.append({int(k): float(v) for k, v in sv.items()})
        else:
            # Fallback: sentence_transformers doesn't natively support sparse output
            # Return empty sparse vectors as placeholder
            for _ in texts:
                results.append({})
        return results

    def model_size_mb(self) -> float:
        self._load()
        if self._backend == "flagembedding":
            return 0.0  # Cannot easily introspect
        total = sum(p.numel() * p.element_size() for p in self._model._modules.values()
                    if hasattr(p, 'parameters'))
        return total / (1024 ** 2)


# ---------------------------------------------------------------------------
# Search and storage utilities
# ---------------------------------------------------------------------------

def run_neural_sparse_search(
    encoder,
    query: str,
    doc_sparse_vecs: List[Dict[int, float]],
    k: int = 10,
) -> List[str]:
    """
    Encode query and rank documents by sparse dot product.
    Returns ranked doc_ids (as strings, index-based).
    """
    query_vec_list = encoder.encode_batch([query])
    query_vec = query_vec_list[0]

    scores = []
    for doc_idx, doc_vec in enumerate(doc_sparse_vecs):
        # Dot product over shared keys
        score = sum(query_vec.get(k, 0.0) * v for k, v in doc_vec.items())
        scores.append((str(doc_idx), score))

    scores.sort(key=lambda x: x[1], reverse=True)
    return [doc_id for doc_id, _ in scores[:k]]


def encode_and_store_corpus(
    conn,
    encoder,
    encoder_name: str,
    table: str = "neural_sparse_vectors",
):
    """
    Encode all documents in the 'documents' table and store sparse vectors
    in the neural_sparse_vectors table (sparsevec column via pgvector).
    """
    if register_vector is not None:
        register_vector(conn)

    with conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {table};")
        cur.execute(f"""
            CREATE TABLE {table} (
                id SERIAL PRIMARY KEY,
                doc_id INT NOT NULL,
                encoder_name TEXT NOT NULL,
                sparse_vec TEXT NOT NULL
            );
        """)
        conn.commit()

        cur.execute("SELECT id, text FROM documents ORDER BY id;")
        rows = cur.fetchall()

    doc_ids = [row[0] for row in rows]
    texts = [row[1] for row in rows]

    batch_size = 32
    for start in range(0, len(texts), batch_size):
        batch_texts = texts[start:start + batch_size]
        batch_ids = doc_ids[start:start + batch_size]
        sparse_vecs = encoder.encode_batch(batch_texts)
        with conn.cursor() as cur:
            for doc_id, sv in zip(batch_ids, sparse_vecs):
                cur.execute(
                    f"INSERT INTO {table} (doc_id, encoder_name, sparse_vec) VALUES (%s, %s, %s)",
                    (doc_id, encoder_name, json.dumps(sv)),
                )
        conn.commit()

    print(f"[encode_and_store_corpus] Stored {len(texts)} sparse vectors for encoder={encoder_name}.")


def cost_estimate(n_docs: int, encoder_name: str, device: str) -> dict:
    """
    Rough cost estimate for encoding n_docs with the given encoder.
    Returns {'encoding_time_sec': float, 'model_size_mb': float}.
    """
    # Throughput benchmarks (docs/sec) — rough empirical estimates
    throughput = {
        "splade-ko": {"cpu": 20.0, "cuda": 200.0},
        "bge-m3-sparse": {"cpu": 15.0, "cuda": 150.0},
    }
    model_sizes = {
        "splade-ko": 440.0,   # ~440 MB
        "bge-m3-sparse": 570.0,  # ~570 MB
    }
    tput = throughput.get(encoder_name, {}).get(device, 10.0)
    encoding_time = n_docs / tput
    model_mb = model_sizes.get(encoder_name, 0.0)
    return {
        "encoding_time_sec": encoding_time,
        "model_size_mb": model_mb,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Phase 4: Neural sparse retrieval (SPLADE-Ko, BGE-M3 sparse)"
    )
    parser.add_argument(
        "--encoder",
        choices=["splade-ko", "bge-m3-sparse"],
        default="splade-ko",
        help="Sparse encoder to use",
    )
    parser.add_argument(
        "--db-url",
        default=os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/dev"),
        help="PostgreSQL connection URL",
    )
    parser.add_argument(
        "--queries-file",
        required=True,
        help="Path to JSON file with queries [{query_id, text, relevant_ids}]",
    )
    parser.add_argument(
        "--output-dir",
        default="results/phase4",
        help="Directory to write result JSON files",
    )
    parser.add_argument(
        "--device",
        choices=["cpu", "cuda"],
        default="cpu",
        help="Device for model inference",
    )
    args = parser.parse_args()

    Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    # Load encoder
    if args.encoder == "splade-ko":
        encoder = SPLADEKoEncoder(device=args.device)
    else:
        encoder = BGEM3SparseEncoder(device=args.device)

    # Load queries
    with open(args.queries_file, encoding="utf-8") as f:
        queries = json.load(f)

    # Connect to DB and encode corpus
    import psycopg2 as _pg
    conn = _pg.connect(args.db_url)
    try:
        encode_and_store_corpus(conn, encoder, args.encoder)

        # Fetch stored sparse vecs for search
        with conn.cursor() as cur:
            cur.execute(
                "SELECT doc_id, sparse_vec FROM neural_sparse_vectors WHERE encoder_name=%s ORDER BY doc_id;",
                (args.encoder,),
            )
            rows = cur.fetchall()
        doc_ids_ordered = [r[0] for r in rows]
        # JSON keys are always strings; convert back to int for dot-product lookup
        doc_sparse_vecs = [{int(k): v for k, v in json.loads(r[1]).items()} for r in rows]

        # Run search for each query
        results = []
        for q in queries:
            ranked_idx = run_neural_sparse_search(encoder, q["text"], doc_sparse_vecs, k=10)
            ranked = [str(doc_ids_ordered[int(i)]) for i in ranked_idx]
            results.append({"query_id": q.get("query_id"), "ranked": ranked})

        output_file = Path(args.output_dir) / f"phase4_{args.encoder}.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"[phase4] Results written to {output_file}")

        # Cost estimate
        est = cost_estimate(len(doc_sparse_vecs), args.encoder, args.device)
        print(f"[phase4] Cost estimate: {est}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
