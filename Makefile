# Korean IR Benchmark Makefile
# Usage: make <target>

DB_URL     ?= postgresql://postgres:postgres@localhost:5432/dev
QUERIES    ?= data/queries.json
OUTPUT_DIR ?= results
DEVICE     ?= cpu
ENCODER    ?= splade-ko
SYSTEM     ?= elasticsearch

.PHONY: help setup data phase1 phase2 phase3 phase4 phase5 report all

## help: Show this help message
help:
	@grep -E '^## [a-zA-Z0-9_-]+:' $(MAKEFILE_LIST) | \
		sed 's/^## //' | \
		awk -F: '{printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

## setup: Install Python dependencies
setup:
	pip install -r requirements.txt

## data: Download and prepare datasets (mMARCO-ko, Namuwiki)
data:
	python3 run_benchmark.py phase0-data

## phase1: Run Phase 1 — PostgreSQL tsvector vs Python BM25
phase1:
	python3 run_benchmark.py phase1 \
		--db-url $(DB_URL) \
		--queries-file $(QUERIES) \
		--output-dir $(OUTPUT_DIR)/phase1

## phase2: Run Phase 2 — Hybrid search (dense + BM25)
phase2:
	python3 run_benchmark.py phase2 \
		--db-url $(DB_URL) \
		--queries-file $(QUERIES) \
		--output-dir $(OUTPUT_DIR)/phase2
	python3 run_benchmark.py phase2-tune \
		--db-url $(DB_URL) \
		--queries-file $(QUERIES) \
		--output-dir $(OUTPUT_DIR)/phase2_tune

## phase3: Run Phase 3 — Analyzer screening + interaction matrix
phase3:
	python3 run_benchmark.py phase3-screen \
		--db-url $(DB_URL) \
		--queries-file $(QUERIES) \
		--output-dir $(OUTPUT_DIR)/phase3
	python3 run_benchmark.py phase3-5-matrix \
		--db-url $(DB_URL) \
		--queries-file $(QUERIES) \
		--output-dir $(OUTPUT_DIR)/phase3_5

## phase4: Run Phase 4 — Neural sparse (SPLADE-Ko, BGE-M3)
phase4:
	python3 run_benchmark.py phase4 \
		--encoder $(ENCODER) \
		--db-url $(DB_URL) \
		--queries-file $(QUERIES) \
		--output-dir $(OUTPUT_DIR)/phase4 \
		--device $(DEVICE)

## phase5: Run Phase 5 — System comparison (ES, Qdrant, PG)
phase5:
	python3 run_benchmark.py phase5 \
		--system $(SYSTEM) \
		--db-url $(DB_URL) \
		--queries-file $(QUERIES) \
		--output-dir $(OUTPUT_DIR)/phase5

## report: Generate summary report from all phase results
report:
	python3 run_benchmark.py report --results-dir $(OUTPUT_DIR)

## all: Run all phases end-to-end
all: data phase1 phase2 phase3 phase4 phase5 report
