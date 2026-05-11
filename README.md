# csegraph

csegraph is a repository context engine for coding agents. It indexes Python code into a SQLite-backed dependency graph, then returns compact, task-specific context bundles before an agent edits.

The product loop is:

```text
index once -> refresh changed files -> retrieve graph-backed context
```

Use csegraph when you want an agent to see the target code, direct dependencies, imports, nearby tests, and a short explanation of why each node was selected without repeatedly scanning the repository.

## Packages

| Package | Location | Purpose |
|---|---|---|
| `csegraph-core` | repo root | Parser, SQLite index, graph traversal, retrieval, CSE metrics, and migrations. Imported as `csegraph_core`. |
| `csegraph` | `packages/csegraph/` | Slim SDK facade over `csegraph_core`. |
| `csegraph-cli` | `packages/csegraph-cli/` | CLI with `index`, `refresh`, `context`, `inspect`, `graph`, and `report`. |

Python imports use underscores, not distribution hyphens: install `csegraph-core`, import `csegraph_core`.

## Install From Source

```bash
env/bin/pip install -e .
env/bin/pip install -e packages/csegraph/
env/bin/pip install -e packages/csegraph-cli/
```

`requirements.txt` contains the same product-only editable installs.

## CLI

```bash
# Build the SQLite graph index.
csegraph index . --json

# Refresh changed/deleted Python files.
csegraph refresh . --json

# Ask for dependency-aware context before an agent edits.
csegraph context "fix auth token refresh bug" --target refresh_token --repo . --json

# Render context for human inspection.
csegraph context "fix auth token refresh bug" --target refresh_token --repo . --format markdown --explain

# Inspect a graph neighborhood.
csegraph inspect refresh_token --repo . --depth 1 --json

# Export a visual HTML graph to .csegraph/csegraph-graph.html.
csegraph graph --repo .

# Generate a project report from the index.
csegraph report . --json
```

By default, the index is stored at `<repo>/.csegraph/index.db`.

Use `--profile small|medium|large` to trade retrieval breadth against speed and token budget. Use `csegraph.json`, `csegraph.toml`, or `--config` to tune thresholds without editing source.

## .csegraphignore

Place a `.csegraphignore` file in the repository root to exclude files and directories from indexing. Supports a `.gitignore`-like subset: blank lines, `#` comments, glob patterns (`*.generated.py`), directory patterns (`data/`), rooted patterns (`/scripts/`), and negation (`!important.py`).

## SDK

```python
from csegraph import ContextService, GraphQueryService, IndexService, RefreshService

IndexService(".csegraph/index.db").index(".", profile="medium")
RefreshService(".csegraph/index.db").refresh(profile="medium")

context = ContextService(".csegraph/index.db").build_context(
    task="fix auth token refresh bug",
    target="refresh_token",
    profile="medium",
)

graph = GraphQueryService(".csegraph/index.db").neighborhood("refresh_token", depth=1)
```

## Context Output

Context JSON includes:

- `schema_version = "csegraph-context-v1"`
- ranked `nodes` with paths, line ranges, reason tags, optional source text, and token estimates
- sufficiency metrics and thresholds
- legacy-compatible fields such as `task`, `target_node_id`, `metrics`, and `context_nodes`

Minor `v1.x` releases may add fields, but they must not remove or rename existing context fields.

## Development

```bash
env/bin/python -m pytest tests/ -q
env/bin/python -m compileall -q csegraph_core packages/csegraph packages/csegraph-cli
env/bin/python -m csegraph_cli --help
```
