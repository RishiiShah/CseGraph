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

All agents use Python AST parsing and avoid executing source files. Outputs are written to the `data/` directory.

## Flow of Development

1. **Ingestion Agent**: parses repository files into typed structural elements.
2. **Linking Agent**: builds a semantic link graph from ingestion output.
3. **Compression Agent** (Done): compresses graph into efficient summaries and context slices.
4. **Context Sufficiency Estimator (CSE)** (Upcoming): decides whether retrieved context is sufficient.
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

3. Run the full pipeline (ingestion -> linking -> compression):
   ```bash
   env/bin/python run_pipeline.py
   ```
   Outputs:
   - `data/ingested_data.json`
   - `data/link_graph.json`
   - `data/compressed_graph.json`

4. Run compression on an existing link graph:
   ```bash
   env/bin/python agents/compression_agent.py
   ```
   Outputs:
   - `data/compressed_graph.json`

5. Run graph integrity tests:
   ```bash
   env/bin/python -m unittest discover -s tests -v
   ```

6. Run sandbox benchmark (single run):
   ```bash
   env/bin/python benchmark_sandboxes.py
   ```
   Outputs:
   - `data/sandbox_benchmark.json`
   - `data/sandbox_benchmark.csv`

7. Run repeated benchmark experiments with CI-friendly aggregates:
   ```bash
   env/bin/python benchmark_repeated.py --repeats 5
   ```
   Outputs:
   - `data/sandbox_benchmark_runs.json`
   - `data/sandbox_benchmark_summary.json`
   - `data/sandbox_benchmark_summary.csv`

8. Generate plots + markdown summary:
   ```bash
   env/bin/python report_plots.py
   ```
   Outputs:
   - `data/plots/*.png`
   - `data/plots/benchmark_summary.md`

9. One-command full report generation (benchmark + repeated + plots):
   ```bash
   env/bin/python run_full_report.py --repeats 5
   ```

## Notes

- Repository filtering and file exclusions are centralized in the ingestion stage.
- The linking stage consumes ingestion output (pipeline order: ingestion -> linking).
