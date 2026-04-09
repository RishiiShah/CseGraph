# Adaptive Memory-Aware Multi-Agent System

Implementation of **"Adaptive Memory-Aware Multi-Agent System with Context Sufficiency Estimation for Repository-Level Code Generation"** — Hitanshu Oza, Rishab Shah (Rutgers University).

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
python -m venv venv
venv\Scripts\activate        # Windows
source venv/bin/activate     # Linux/Mac

# Install dependencies
venv/Scripts/pip install -r requirements.txt

# Set Groq API key (required for code generation)
# Create a .env file in the project root:
echo GROQ_API_KEY=your_key_here > .env
```

---

## Run Commands

### Full pipeline

```bash
# Run on ALL sandboxes — generates one .py file per sandbox (DEFAULT)
venv/Scripts/python run_pipeline.py

# Same with explicit flags
venv/Scripts/python run_pipeline.py --all-sandboxes --output-dir data/results

# Run on a SINGLE sandbox
venv/Scripts/python run_pipeline.py --root-dir tests/fixtures/sandboxes/graph_analytics --output-dir data/graph_out

# CSE only, skip code generation (no API key needed)
venv/Scripts/python run_pipeline.py --all-sandboxes --skip-codegen
```

### Individual agents

```bash
# Step 1 — Ingestion
venv/Scripts/python agents/ingestion_agent.py --root-dir tests/fixtures/sandboxes/graph_analytics

# Step 2 — Linking
venv/Scripts/python agents/linking_agent.py --root-dir tests/fixtures/sandboxes/graph_analytics

# Step 3 — Compression
venv/Scripts/python agents/compression_agent.py \
    --graph-path data/link_graph.json \
    --output-path data/compressed_graph.json

# Step 4 — CSE
venv/Scripts/python agents/cse_agent.py \
    --link-graph data/link_graph.json \
    --compressed-graph data/compressed_graph.json

# Step 4 — CSE with custom query
venv/Scripts/python agents/cse_agent.py \
    --link-graph data/link_graph.json \
    --compressed-graph data/compressed_graph.json \
    --query "Implement class UserService with create_user and get_user methods"

# Step 5 — Code Generation
venv/Scripts/python agents/code_gen_agent.py \
    --link-graph data/link_graph.json \
    --compressed-graph data/compressed_graph.json \
    --cse-result data/cse_result.json \
    --output data/code_gen_result.json
```

### Benchmarking

```bash
# Single-run benchmark (ingestion + linking metrics per sandbox)
venv/Scripts/python benchmark_sandboxes.py

# Repeated benchmark with statistical aggregates (mean, std, CI95)
venv/Scripts/python benchmark_repeated.py --repeats 5

# Full report: benchmark + repeated + plots
venv/Scripts/python run_full_report.py --repeats 5
```

### Tests

```bash
venv/Scripts/python -m pytest tests/ -v
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

## Validated Results (6/6 sandboxes)

| Sandbox | sufficient | dep | ent | sem | conf | rounds | generated file |
|---|---|---|---|---|---|---|---|
| baseline_import_resolution | True | 100% | 100% | 8% | 81% | 2/3 | `generated_UserService.py` |
| etl_pipeline_oop | True | 100% | 100% | 5% | 81% | 2/3 | `generated_Pipeline.py` |
| event_driven_orders | True | 100% | 100% | 9% | 82% | 2/3 | `generated_run_demo.py` |
| graph_analytics | True | 100% | 100% | 10% | 81% | 2/3 | `generated_GraphQueryEvaluator.py` |
| nlp_chunking_pipeline | True | 100% | 83% | 8% | 75% | 1/3 | `generated_RAGPipeline.py` |
| user_service_api | True | 100% | 83% | 9% | 75% | 1/3 | `generated_UserService.py` |

---

## Notes

- All pipeline steps use Python AST — no source files are executed.
- Groq API key is only required for Step 5 (code generation). Steps 1–4 run fully offline.
- `--skip-codegen` runs the full structural pipeline without calling any LLM.
- The CSE uses BM25 with code-aware tokenisation — no scikit-learn, no GPU, no embeddings.
