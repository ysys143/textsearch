"""
run_benchmark.py — Unified entrypoint for all benchmark phases.

Usage:
    python3 run_benchmark.py <subcommand> [options]

Subcommands:
    phase0-data       Acquire and prepare datasets
    phase1            Phase 1: tsvector BM25 baseline
    phase2            Phase 2: hybrid search (dense + BM25)
    phase2-tune       Phase 2.5: hyperparameter tuning
    phase3-screen     Phase 3: tokenizer / analyzer screening
    phase3-5-matrix   Phase 3.5: interaction matrix
    phase4            Phase 4: neural sparse (SPLADE-Ko, BGE-M3)
    phase5            Phase 5: system comparison (ES, Qdrant, PG)
    report            Generate summary report
"""

import argparse
import os
import sys
from pathlib import Path

# Ensure project root is on the path
sys.path.insert(0, str(Path(__file__).parent))


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def cmd_phase0_data(args):
    """Acquire and prepare datasets (mMARCO-ko, Namuwiki)."""
    try:
        from experiments import phase0_data
        phase0_data.main()
    except ImportError:
        print("[phase0-data] experiments/phase0_data.py not found — skipping.")


def cmd_phase1(args):
    """Phase 1: PostgreSQL tsvector vs Python BM25 baseline."""
    from experiments.phase1_tsvector import main as _main
    # Forward remaining argv to the sub-module's argparse
    _patch_argv(args, "phase1")
    _main()


def cmd_phase2(args):
    """Phase 2: BM25 implementations (plpgsql, pgvector-sparse, pg_bm25)."""
    try:
        from experiments.phase2_bm25_impl import main as _main
        _patch_argv(args, "phase2")
        _main()
    except ImportError:
        print("[phase2] experiments/phase2_bm25_impl.py not found.")


def cmd_phase2_tune(args):
    """Phase 2.5: BM25 parameter sensitivity (k1, b grid search)."""
    try:
        from experiments.phase2_5_param_tuning import main as _main
        _patch_argv(args, "phase2-tune")
        _main()
    except ImportError:
        print("[phase2-tune] experiments/phase2_5_param_tuning.py not found.")


def cmd_phase3_screen(args):
    """Phase 3 Tier 1: Analyzer / tokenizer screening."""
    try:
        from experiments.phase3_analyzer_screen import main as _main
        _patch_argv(args, "phase3-screen")
        _main()
    except ImportError:
        print("[phase3-screen] experiments/phase3_analyzer_screen.py not found.")


def cmd_phase3_5_matrix(args):
    """Phase 3.5: Interaction matrix across tokenizers and retrieval methods."""
    try:
        from experiments.phase3_5_interaction_matrix import main as _main
        _patch_argv(args, "phase3-5-matrix")
        _main()
    except ImportError:
        print("[phase3-5-matrix] experiments/phase3_5_interaction_matrix.py not found.")


def cmd_phase4(args):
    """Phase 4: Neural sparse retrieval (SPLADE-Ko, BGE-M3 sparse)."""
    from experiments.phase4_neural_sparse import main as _main
    _patch_argv(args, "phase4")
    _main()


def cmd_phase5(args):
    """Phase 5: System comparison (Elasticsearch, Qdrant, PostgreSQL)."""
    from experiments.phase5_system_comparison import main as _main
    _patch_argv(args, "phase5")
    _main()


def cmd_report(args):
    """Generate a summary report from all phase results."""
    try:
        from benchmark.report import generate_comparison_table, load_results
        results_dir = getattr(args, "results_dir", "results")
        results = load_results(results_dir)
        if results:
            print(generate_comparison_table(results))
        else:
            _generate_simple_report(args)
    except (ImportError, Exception):
        _generate_simple_report(args)


def _generate_simple_report(args):
    """Minimal fallback report: collect all result JSONs and print a table."""
    import json
    results_dir = Path(getattr(args, "results_dir", "results"))
    print("\n=== Benchmark Summary Report ===\n")
    json_files = sorted(results_dir.rglob("*.json"))
    if not json_files:
        print(f"No result files found in {results_dir}")
        return
    for f in json_files:
        try:
            with open(f) as fh:
                data = json.load(fh)
            method = data.get("method", f.stem)
            ndcg = data.get("ndcg_at_10", "N/A")
            recall10 = data.get("recall_at_10", "N/A")
            mrr = data.get("mrr", "N/A")
            print(f"  {method:<40} nDCG@10={ndcg:.4f}  R@10={recall10:.4f}  MRR={mrr:.4f}")
        except Exception:
            pass
    print()


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _patch_argv(args, subcommand: str):
    """
    Replace sys.argv so sub-module argparse sees only the forwarded args.
    args.remainder contains everything after the subcommand.
    """
    remainder = getattr(args, "remainder", []) or []
    sys.argv = [f"run_benchmark.py {subcommand}"] + remainder


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_benchmark.py",
        description="Korean IR Benchmark — unified entrypoint for all phases.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="subcommand", metavar="<subcommand>")
    subparsers.required = True

    def _add(name, help_text, handler):
        sp = subparsers.add_parser(name, help=help_text)
        sp.set_defaults(func=handler)
        sp.add_argument(
            "remainder",
            nargs=argparse.REMAINDER,
            help="Arguments forwarded to the phase module",
        )
        return sp

    _add("phase0-data",     "Acquire and prepare datasets (mMARCO-ko, Namuwiki)",        cmd_phase0_data)
    _add("phase1",          "Phase 1: tsvector BM25 baseline",                           cmd_phase1)
    _add("phase2",          "Phase 2: hybrid search (dense + BM25)",                     cmd_phase2)
    _add("phase2-tune",     "Phase 2.5: hyperparameter tuning for hybrid weights",       cmd_phase2_tune)
    _add("phase3-screen",   "Phase 3 Tier 1: tokenizer/analyzer screening",              cmd_phase3_screen)
    _add("phase3-5-matrix", "Phase 3.5: interaction matrix",                             cmd_phase3_5_matrix)
    _add("phase4",          "Phase 4: neural sparse (SPLADE-Ko, BGE-M3)",                cmd_phase4)
    _add("phase5",          "Phase 5: system comparison (ES, Qdrant, PG)",               cmd_phase5)

    rp = subparsers.add_parser("report", help="Generate summary report from phase results")
    rp.set_defaults(func=cmd_report)
    rp.add_argument("--results-dir", default="results", help="Root results directory")
    rp.add_argument(
        "remainder",
        nargs=argparse.REMAINDER,
        help="Arguments forwarded to the report module",
    )

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
