# Adaptive Memory-Aware Multi-Agent System

Implementation of **"Adaptive Memory-Aware Multi-Agent System with Context Sufficiency Estimation for Repository-Level Code Generation"** — Hitanshu Oza, Rishabh Shah (Rutgers University).

---

## Architecture

The pipeline follows a five-stage multi-agent design:

```
Repository
    │
    ▼
[Ingestion Agent]       — Parse Python source into structured nodes
    │
    ▼
[Linking Agent]         — Build a dependency graph (calls, imports, contains)
    │
    ▼
[Compression Agent]     — Summarise graph nodes; pre-compute context slices
    │
    ▼
[CSE Agent]             — Evaluate context sufficiency; expand if needed
    │  is_sufficient = True
    ▼
[Code Generation Agent] — Generate code using a Groq-hosted LLM
    │
    ▼
generated_<Target>.py
```

---

## Implemented Components

### 1. Ingestion Agent (`agents/ingestion_agent.py`)
Parses Python repositories using AST into structured `FileNode` and `CodeNode` objects. Extracts function signatures, class definitions, import statements, and line ranges.

### 2. Linking Agent (`agents/linking_agent.py`)
Consumes ingestion output and builds a `LinkGraph` with three edge types:
- `contains` — file → symbol containment
- `imports` — cross-file import resolution
- `calls` — symbol-to-symbol call edges

### 3. Compression Agent (`agents/compression_agent.py`)
Compresses the link graph into a `CompressedGraph` with:
- Per-node text summaries (name, type, connectivity)
- High-degree hub identification (top-20 by combined degree)
- Pre-computed context slices at radius 1 and 2 for each hub

### 4. Context Sufficiency Estimator (`agents/cse_agent.py`)
Evaluates retrieved context against four metrics before allowing generation:

| Metric | Threshold | Description |
|---|---|---|
| `dependency_completeness` | ≥ 80% | Weighted ratio of Tier-0/Tier-1 deps present in context |
| `entity_coverage` | ≥ 80% | Fraction of query code-entities found in context node names |
| `semantic_overlap` | ≥ 5% (relaxed) / 50% (strict) | BM25 similarity with code-aware tokenisation |
| `model_confidence` | ≥ 70% | Composite proxy: 0.45·dep + 0.35·ent + 0.20·sem |

**Tiered expansion strategy:**
- **Tier 0** (direct `calls`): always seeded at 100% before round 0
- **Tier 1** (file-level `imports`): up to `IMPORT_BUDGET=20` nodes
- **Tier 2** (2-hop BFS): budget-limited to `CONTEXT_BUDGET=60` total nodes

**Raw Code Fallback:** when `model_confidence < 0.70`, Tier-0 nodes have their compressed summaries replaced with verbatim source code.

**Semantic similarity:** Okapi BM25 with CamelCase/snake_case identifier splitting — no GPU or embedding model required.

### 5. Code Generation Agent (`agents/code_gen_agent.py`)
Generates Python code using a Groq-hosted LLM (`llama-3.3-70b-versatile`), gated by the CSE:
- Only executes when `cse_result.is_sufficient = True`
- Prompt assembles: compressed summaries + verbatim raw code for low-confidence nodes
- Saves both a structured JSON result and a clean `.py` source file

---

## Setup

```bash
# Create and activate virtual environment
python -m venv env
source env/bin/activate      # Linux/Mac
env\Scripts\activate         # Windows

# Install dependencies
env/bin/pip install -r requirements.txt

# Set Groq API key (required for code generation)
# Create a .env file in the project root:
echo GROQ_API_KEY=your_key_here > .env
```

---

## Local Model Setup (Optional)

To use local GGUF models instead of Groq API for code generation:

### 1. Download GGUF Models

The system supports Qwen models. Download the desired quantized model:

```bash
# Create codermodel directory if it doesn't exist
mkdir -p codermodel

# Option A: Qwen 3.5 4B model (faster, lower memory)
wget -O codermodel/Qwen3.5-4B.Q4_K_M.gguf \
  https://huggingface.co/Qwen/Qwen3.5-4B-GGUF/resolve/main/Qwen3.5-4B.Q4_K_M.gguf

# Option B: Qwen 3.5 9B model (better quality, more memory intensive)
wget -O codermodel/Qwen3.5-9B.Q4_K_M.gguf \
  https://huggingface.co/Qwen/Qwen3.5-9B-GGUF/resolve/main/Qwen3.5-9B.Q4_K_M.gguf
```

### 2. Automatic Detection

Once models are placed in `codermodel/`, the pipeline automatically:
- Detects available hardware (Metal/CUDA/ROCm/CPU)
- Selects the best-available model
- Loads it without requiring a Groq API key

### 3. Run Without Groq API

```bash
# Pipeline will use local model if available (no GROQ_API_KEY needed)
env/bin/python run_pipeline.py --all-sandboxes
```

**Note:** If no local model is found in `codermodel/`, the pipeline falls back to Groq API (requires `GROQ_API_KEY`).

### 4. Hardware Support

- **Apple Silicon (Metal)**: Optimized GPU acceleration via Metal
- **NVIDIA (CUDA)**: GPU acceleration if CUDA toolkit is available
- **AMD (ROCm)**: GPU acceleration if ROCm is installed
- **CPU**: Falls back to CPU if no GPU is available

---

## Run Commands

### Full pipeline

```bash
# Run on ALL sandboxes — generates one .py file per sandbox (DEFAULT)
env/bin/python run_pipeline.py

# Same with explicit flags
env/bin/python run_pipeline.py --all-sandboxes --output-dir data/results

# Run on a SINGLE sandbox
env/bin/python run_pipeline.py --root-dir tests/fixtures/sandboxes/graph_analytics --output-dir data/graph_out

# CSE only, skip code generation (no API key needed)
env/bin/python run_pipeline.py --all-sandboxes --skip-codegen
```

### Individual agents

```bash
# Step 1 — Ingestion
env/bin/python agents/ingestion_agent.py --root-dir tests/fixtures/sandboxes/graph_analytics

# Step 2 — Linking
env/bin/python agents/linking_agent.py --root-dir tests/fixtures/sandboxes/graph_analytics

# Step 3 — Compression
env/bin/python agents/compression_agent.py \
    --graph-path data/link_graph.json \
    --output-path data/compressed_graph.json

# Step 4 — CSE
env/bin/python agents/cse_agent.py \
    --link-graph data/link_graph.json \
    --compressed-graph data/compressed_graph.json

# Step 4 — CSE with custom query
env/bin/python agents/cse_agent.py \
    --link-graph data/link_graph.json \
    --compressed-graph data/compressed_graph.json \
    --query "Implement class UserService with create_user and get_user methods"

# Step 5 — Code Generation
env/bin/python agents/code_gen_agent.py \
    --link-graph data/link_graph.json \
    --compressed-graph data/compressed_graph.json \
    --cse-result data/cse_result.json \
    --output data/code_gen_result.json
```

### Benchmarking

```bash
# Single-run benchmark (ingestion + linking metrics per sandbox)
env/bin/python benchmark_sandboxes.py

# Repeated benchmark with statistical aggregates (mean, std, CI95)
env/bin/python benchmark_repeated.py --repeats 5

# Full report: benchmark + repeated + plots
env/bin/python run_full_report.py --repeats 5

# Baseline comparison: adaptive vs full_context vs static_rag (with code generation)
env/bin/python compare_baselines.py --output-dir data

# Baseline comparison without code generation (faster, no API key needed)
env/bin/python compare_baselines.py --output-dir data --skip-codegen

# Generate plots from comparison results
env/bin/python report_plots.py \
    --baseline-csv data/baseline_comparison.csv \
    --baseline-summary data/baseline_summary.json
```

### Tests

```bash
env/bin/python -m pytest tests/ -v
```

---

## Pipeline Outputs

Each sandbox run writes to its output directory (e.g., `data/results/graph_analytics/`):

| File | Contents |
|---|---|
| `ingested_data.json` | Parsed file and symbol nodes |
| `link_graph.json` | Full dependency graph (nodes + edges) |
| `compressed_graph.json` | Node summaries + pre-computed context slices |
| `cse_result.json` | Sufficiency evaluation: metrics, context nodes, raw code nodes |
| `code_gen_result.json` | Full generation record: model, tokens, node IDs used |
| `generated_<Target>.py` | Generated Python source with metadata header |

### `cse_result.json` fields

| Field | Description |
|---|---|
| `is_sufficient` | Whether all four thresholds passed |
| `metrics` | `dependency_completeness`, `entity_coverage`, `semantic_overlap`, `model_confidence` |
| `context_node_ids` | Final set of nodes passed to the code generator |
| `raw_code_nodes` | Nodes where compressed summary was replaced by verbatim source |
| `expansion_rounds` | How many times CSE expanded context before deciding |
| `recompressed_rounds` | How many times recompression was triggered by low logprob |
| `unit_test_pass_rate` | Fraction of sandbox unit tests passed by generated code (0–1) |
| `reason` | `"All thresholds met"` or `"Max expansion rounds reached"` |
| `thresholds` | The threshold values used for this run |
| `query` | The original query text and target node |

### `generated_<Target>.py` header

Every generated file includes a metadata header:
```python
# Generated by CodeGenAgent
# Model   : llama-3.3-70b-versatile
# Target  : symbol::metrics/evaluator.py::class::GraphQueryEvaluator
# CSE     : sufficient=True, rounds=2, context_nodes=17, raw_code_nodes=3
# Tokens  : prompt=759, completion=391
```

---

## Baseline Comparison Results (7 sandboxes × 3 strategies)

Three strategies are compared across all sandboxes. Numbers below are **means** across targets per sandbox.

> Re-run `env/bin/python compare_baselines.py` to regenerate fresh results.

### Adaptive strategy — per-sandbox breakdown

| Sandbox | dep% | ent% | conf% | exp rounds | context nodes | compile% | unit_test% |
|---|---|---|---|---|---|---|---|
| baseline_import_resolution | 100% | 100% | 81.6% | 2.0 | 27.0 | 100% | 33.3% |
| etl_pipeline_oop | 100% | 100% | 81.5% | 1.7 | 18.3 | 100% | 66.7% |
| event_driven_orders | 100% | 100% | 83.2% | 2.0 | 33.7 | 100% | 93.9% |
| graph_analytics | 100% | 100% | 81.5% | 2.0 | 17.7 | 100% | 66.7% |
| ml_training_pipeline | 100% | 90.7% | 80.3% | 1.3 | 42.7 | 100% | 100% |
| nlp_chunking_pipeline | 100% | 94.4% | 79.9% | 1.7 | 21.0 | 100% | 98.0% |
| user_service_api | 100% | 94.4% | 80.3% | 1.7 | 25.3 | 100% | 88.9% |

### Strategy comparison — global means (7 sandboxes × 3 targets each)

| Strategy | context nodes | exp rounds | dep% | ent% | conf% | prompt tokens† | compile% | unit_test% |
|---|---|---|---|---|---|---|---|---|
| **adaptive** | **26.5** | **1.76** | **100%** | 97.1% | **81.2%** | 963 | **100%** | 78.2% |
| full_context | 33.7 | 0.0 | 87.6% | **100%** | 76.7% | 1179 | **100%** | **90.2%** |
| static_rag | 19.7 | 0.0 | 93.7% | 96.6% | 75.3% | **686** | **100%** | 72.7% |

†Prompt tokens averaged over sandboxes where codegen ran (compile% = 100% for all).

**Key takeaways:**
- Adaptive achieves **100% dep_completeness** on every sandbox; full_context averages 87.6%, static_rag 93.7%
- Adaptive uses **18% fewer tokens** than full_context (963 vs 1179) while maintaining higher model confidence (81.2% vs 76.7%)
- The `ml_training_pipeline` sandbox (55+ symbols, 4 packages) shows the largest gap: static_rag dep=73.8% vs adaptive dep=100%, confirming adaptive CSE's advantage on larger, more complex repos
- `efficiency_scatter.png` in `data/plots/` visualises the token-vs-correctness trade-off across all three strategies

### Analysis: `baseline_import_resolution` per-target breakdown

The `baseline_import_resolution` sandbox has the largest variance across targets (3 targets × 3 strategies). Per-target pass rates reveal why the adaptive global mean is low (33.3%):

| Target | adaptive | full_context | static_rag |
|---|---|---|---|
| `pkg/service.py::UserService` | **18/18 (100%)** | 0/1 (crash) | 13/18 (72.2%) |
| `pkg/formatter.py::DefaultPayloadFormatter` | 0/1 (crash) | **18/18 (100%)** | 12/18 (66.7%) |
| `pkg/utils.py::caller` | 0/1 (crash) | **17/18 (94.4%)** | 0/1 (crash) |

The "0/1 crash" entries are **import errors in the generated code**, not logic failures — the generated file is syntactically valid (ast.parse passes) but contains incorrect import paths or missing helper definitions:

- Adaptive on `formatter.py`: generates `from contracts import PayloadFormatter` (wrong package prefix — should be `from pkg.contracts`)
- Adaptive on `utils.py`: omits `helper()` function — test file's `from pkg.utils import helper` fails at collection
- Full-context on `service.py`: generates `from pkg.utils import risk_process` (`risk_process` lives in `pkg.metrics`, not `pkg.utils`)

**Root cause: LLM import path hallucination**, not CSE context insufficiency. Adaptive's dep_completeness is 100% for this sandbox — the right context was gathered. The generation model infers incorrect module locations for small files with simple contracts. Full-context wins by providing verbatim source for every node (raw_code_nodes=19 = context_node_count), letting the LLM copy import paths exactly.

**Implication:** For tiny sandboxes with exact literal returns and short import paths, verbatim raw code is more reliable than compressed summaries. Adaptive's compressed-summary approach is optimised for large, dependency-heavy repos (`ml_training_pipeline` dep=100% vs static_rag 73.8%) but may over-abstract context for micro-repos.

---

## Plots (`data/plots/`)

| File | Description |
|---|---|
| `baseline_context_nodes.png` | Mean context node count by strategy |
| `baseline_token_efficiency.png` | Mean prompt tokens by strategy |
| `baseline_cse_metrics.png` | dep%, ent%, semantic overlap, conf% by strategy |
| `baseline_compile_rate.png` | Compile success rate by strategy |
| `baseline_unit_test_pass_rate.png` | Global unit test pass rate by strategy |
| `baseline_expansion_rounds.png` | Mean CSE expansion rounds by strategy |
| `per_sandbox_context_nodes.png` | Context nodes per sandbox × strategy |
| `per_sandbox_entity_coverage.png` | Entity coverage per sandbox × strategy |
| `per_sandbox_unit_test_pass_rate.png` | Unit test pass rate per sandbox × strategy |
| `efficiency_scatter.png` | Token cost vs correctness scatter (3 strategy points) |

---

## Design Decisions

**Semantic similarity via BM25, not embeddings.** The CSE uses Okapi BM25 with CamelCase/snake_case identifier splitting rather than embedding cosine similarity. This avoids GPU dependency, reduces latency, and handles sparse code-identifier vocabularies where token overlap is more informative than distributional similarity. The threshold is relaxed (5%) to prevent false negatives on short function signatures.

**Synthetic sandboxes as the evaluation environment.** The pipeline is evaluated on 7 hand-crafted sandboxes rather than a public benchmark (e.g., SWE-bench, RepoEval). This provides controlled ground truth — exact reference implementations, deterministic unit tests, and reproducible dependency graphs — without license or API constraints. Each sandbox covers a distinct structural pattern (OOP hierarchies, event-driven dispatch, ML training loops, import resolution chains) to stress-test different aspects of the retrieval pipeline.

**What adaptive wins and where it does not.** Adaptive CSE achieves 100% dependency completeness on every sandbox (vs. 87.6% for full_context, 93.7% for static_rag) and uses 18% fewer prompt tokens than full_context. On large, dependency-heavy repos (`ml_training_pipeline`: 55+ symbols, 4 packages), the advantage is decisive — static_rag dep=73.8% vs. adaptive dep=100%. On micro-repos with short import paths and literal return values (`baseline_import_resolution`), full_context's verbatim source inclusion is more reliable because import path precision matters more than dependency coverage. Compressed summaries trade away literal exactness for scalability; this tradeoff favours adaptive as repo size grows.

---

## Notes

- All pipeline steps use Python AST — no source files are executed.
- Groq API key is only required for Step 5 (code generation). Steps 1–4 run fully offline.
- `--skip-codegen` runs the full structural pipeline without calling any LLM.
- The CSE uses BM25 with code-aware tokenisation — no scikit-learn, no GPU, no embeddings.
- `system_profile.py` detects Metal/CUDA/ROCm/CPU and selects the best local GGUF model when one is placed in `codermodel/`. It also supplies the embedding device for optional sentence-transformers. Groq API is used when no local model is present.
