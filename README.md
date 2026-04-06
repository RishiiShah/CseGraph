# Adaptive Memory-Aware Multi Agent System

This project is an implementation of an "Adaptive Memory-Aware Multi Agent System with Context Sufficiency Estimation for Repository-Level Code Generation".

## Current State

Implemented components:
- **Ingestion Agent**: parses Python repositories into structured `FileNode` and `CodeNode` objects.
- **Linking Agent**: consumes ingestion output and builds a repository link graph with:
  - file -> symbol containment edges,
  - local import edges,
  - symbol call edges.
- **Compression Agent**: compresses the link graph into memory-aware summaries with:
  - node-level summaries (purpose, dependencies, dependents),
  - high-degree hub identification,
  - multi-radius context slices for efficient retrieval.
- **Context Sufficiency Estimator (CSE)**: evaluates whether retrieved context is sufficient for code generation using three metrics:
  - **Dependency Completeness** (≥ 80%): ratio of resolved call/import dependencies in context.
  - **Entity Coverage** (≥ 80%): ratio of query-mentioned entities found in context.
  - **Semantic Overlap** (≥ 50%): TF-IDF cosine similarity between query and context summaries.
  - If any metric fails, CSE expands context (increases BFS radius + adds missing deps) up to 3 rounds.

All agents use Python AST parsing and avoid executing source files. Outputs are written to the `data/` directory.

## Flow of Development

1. **Ingestion Agent**: parses repository files into typed structural elements.
2. **Linking Agent**: builds a semantic link graph from ingestion output.
3. **Compression Agent** (Done): compresses graph into efficient summaries and context slices.
4. **Context Sufficiency Estimator (CSE)** (Done): decides whether retrieved context is sufficient.
5. **Code Generation Agent** (Upcoming): generates code only after sufficiency checks pass.

## How to Run

Use your virtual environment Python executable:

0. Install dependencies:
   ```bash
   env/bin/pip install -r requirements.txt
   ```

1. Run ingestion:
   ```bash
   env/bin/python agents/ingestion_agent.py
   ```
   Output: `data/ingested_data.json`

2. Run linking:
   ```bash
   env/bin/python agents/linking_agent.py
   ```
   Output: `data/link_graph.json`

3. Run the full pipeline (ingestion -> linking -> compression -> CSE):
   ```bash
   env/bin/python run_pipeline.py
   ```
   Outputs:
   - `data/ingested_data.json`
   - `data/link_graph.json`
   - `data/compressed_graph.json`
   - `data/cse_result.json`

4. Run compression on an existing link graph:
   ```bash
   env/bin/python agents/compression_agent.py
   ```
   Outputs:
   - `data/compressed_graph.json`

5. Run CSE on existing graph data:
   ```bash
   env/bin/python agents/cse_agent.py
   ```
   Output: `data/cse_result.json`

   With a custom query:
   ```bash
   env/bin/python agents/cse_agent.py --query "Generate code related to UserService"
   ```

6. Run graph integrity tests:
   ```bash
   env/bin/python -m pytest tests/ -v
   ```

7. Run sandbox benchmark (single run):
   ```bash
   env/bin/python benchmark_sandboxes.py
   ```
   Outputs:
   - `data/sandbox_benchmark.json`
   - `data/sandbox_benchmark.csv`

8. Run repeated benchmark experiments with CI-friendly aggregates:
   ```bash
   env/bin/python benchmark_repeated.py --repeats 5
   ```
   Outputs:
   - `data/sandbox_benchmark_runs.json`
   - `data/sandbox_benchmark_summary.json`
   - `data/sandbox_benchmark_summary.csv`

9. Generate plots + markdown summary:
   ```bash
   env/bin/python report_plots.py
   ```
   Outputs:
   - `data/plots/*.png`
   - `data/plots/benchmark_summary.md`

10. One-command full report generation (benchmark + repeated + plots):
    ```bash
    env/bin/python run_full_report.py --repeats 5
    ```

## Pipeline Output: `cse_result.json`

The CSE produces a JSON result with:
- **`is_sufficient`**: whether the gathered context passes all thresholds.
- **`metrics`**: the three sufficiency scores (dependency_completeness, entity_coverage, semantic_overlap).
- **`context_node_ids`**: the final list of code nodes included in the context — this is what gets passed to the Code Generation Agent.
- **`expansion_rounds`**: how many times the CSE expanded context before reaching a decision.
- **`reason`**: human-readable explanation ("All thresholds met" or "Max expansion rounds reached").

## Notes

- Repository filtering and file exclusions are centralized in the ingestion stage.
- The pipeline order is: Ingestion -> Linking -> Compression -> CSE.
- The CSE uses scikit-learn's TF-IDF for semantic overlap (no GPU or API keys required).
