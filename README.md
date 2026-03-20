# Adaptive Memory-Aware Multi Agent System

This project is an implementation of an "Adaptive Memory-Aware Multi Agent System with Context Sufficiency Estimation for Repository-Level Code Generation".

## Current State

Implemented components:
- **Ingestion Agent**: parses Python repositories into structured `FileNode` and `CodeNode` objects.
- **Linking Agent**: consumes ingestion output and builds a repository link graph with:
  - file -> symbol containment edges,
  - local import edges,
  - symbol call edges.

Both agents use Python AST parsing and avoid executing source files. Outputs are written to the `data/` directory.

## Flow of Development

1. **Ingestion Agent**: parses repository files into typed structural elements.
2. **Linking Agent**: builds a semantic link graph from ingestion output.
3. **Compression Agent** (Upcoming): summarizes selected graph/context slices.
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

3. Run the full pipeline (ingestion -> linking):
   ```bash
   env/bin/python run_pipeline.py
   ```
   Outputs:
   - `data/ingested_data.json`
   - `data/link_graph.json`

4. Run graph integrity tests:
   ```bash
   env/bin/python -m unittest discover -s tests -v
   ```

## Notes

- Repository filtering and file exclusions are centralized in the ingestion stage.
- The linking stage consumes ingestion output (pipeline order: ingestion -> linking).
