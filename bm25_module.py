# Compatibility shim — actual code has moved to src/bm25_module.py
# This file exists only for backward compatibility with old import paths.
# ruff: noqa: F401, F403
from src.bm25_module import *
from src.bm25_module import (
    BM25Embedder,
    BM25Embedder_PG,
    execute_query,
    init_text_embedding_table,
    init_inverted_index_table,
    create_pg_function_bm25_ranking,
    rebuild_inverted_index,
    _build_tokenizer,
    compute_idf_dict,
    load_corpus_from_db,
    fit_bm25_from_corpus,
    bm25_sparse_search,
    bm25_sql_search,
    cosine_search,
    hybrid_search_linear,
    hybrid_search_rrf,
    setup_sparse_bm25_table,
    SAMPLE_SENTENCES,
    KIWI_CONTENT_POS,
    DB_CONFIG,
)
