# csegraph

csegraph is a repository context engine for coding agents. It indexes source code into a SQLite-backed dependency graph, then returns compact, task-specific context bundles before an agent edits.

The product loop is:

```text
index once -> refresh changed files -> retrieve graph-backed context
```

Use csegraph when you want an agent to see the target code, direct dependencies, imports, nearby tests, and a short explanation of why each node was selected without repeatedly scanning the repository.

## Packages

| Package | Location | Purpose |
|---|---|---|
| `csegraph-core` | repo root | Parser, SQLite index, graph traversal, retrieval, and CSE metrics. Imported as `csegraph_core`. |
| `csegraph` | `packages/csegraph/` | Slim SDK facade over `csegraph_core`. |
| `csegraph-cli` | `packages/csegraph-cli/` | CLI with indexing, refresh, retrieval, graph inspection, reports, maintenance commands, and MCP stdio serving. |

Python imports use underscores, not distribution hyphens: install `csegraph-core`, import `csegraph_core`.

## Install From Source

```bash
env/bin/pip install -e .
env/bin/pip install -e packages/csegraph/
env/bin/pip install -e packages/csegraph-cli/
```

`requirements.txt` contains the same product-only editable installs.

## Base Commands

```bash
csegraph index                    # Build the repository index
csegraph refresh                  # Incremental refresh for changed/deleted files
csegraph context "task"           # Retrieve context (detail_level=auto: minimal if sufficient, else standard)
csegraph context "task" --detail-level standard  # Request working context with source
csegraph context "task" --detail-level full --explain  # Full context with explanations
csegraph context "task" --target symbol --format markdown
csegraph status --verbose         # Graph health and staleness
csegraph postprocess              # Rebuild FTS and communities without re-parsing
csegraph inspect symbol --depth 1 # Graph neighborhood
csegraph path source target       # Shortest path between nodes
csegraph graph                    # Generate interactive HTML graph
csegraph tree                     # Generate interactive HTML file tree
csegraph communities              # Detect graph communities
csegraph report --json            # Structural report
csegraph hooks install            # Install git auto-refresh hooks
csegraph watch                    # Auto-refresh on file changes
csegraph benchmark --target symbol
csegraph serve                    # Start MCP stdio server
csegraph install                  # Configure local MCP client files
csegraph install --platform cursor --dry-run --json
```

By default, the index is stored at `<repo>/.csegraph/index.db`.

Use `--profile small|medium|large` to trade retrieval breadth against speed and token budget. Use `csegraph.json`, `csegraph.toml`, or `--config` to tune thresholds without editing source.

AI assistants can call these MCP tools after `csegraph serve` is configured by the client. `csegraph install` writes stdio MCP configuration for supported clients; use `--platform codex|cursor|claude-code|gemini-cli|kiro|copilot` to target one client.

| Tool | Description |
|---|---|
| `csegraph_index` | Build a repository SQLite graph index. |
| `csegraph_refresh` | Refresh changed/deleted files in an existing index. |
| `csegraph_context` | Retrieve compact task-specific context. |
| `csegraph_graph` | Inspect a graph neighborhood around a node. |
| `csegraph_path` | Find the shortest path between two nodes. |
| `csegraph_tree` | Export an interactive HTML file tree. |
| `csegraph_communities` | Detect dependency graph communities. |
| `csegraph_report` | Generate a structural report from the index. |

MCP prompts are workflow templates that clients may expose as slash commands.

| Prompt | Workflow |
|---|---|
| `csegraph-index` | Ask the agent to build the graph with `csegraph_index`. |
| `csegraph-refresh` | Ask the agent to refresh changed files with `csegraph_refresh`. |
| `csegraph-context` | Retrieve task-specific context with `csegraph_context`. |
| `csegraph-review` | Review changes with context, report, and graph tools. |
| `csegraph-architecture` | Map architecture from report, communities, and graph data. |
| `csegraph-pre-merge` | Run a pre-merge context and risk checklist. |

## .csegraphignore

Place a `.csegraphignore` file in the repository root to exclude files and directories from indexing. Supports a `.gitignore`-like subset: blank lines, `#` comments, glob patterns (`*.generated.py`), directory patterns (`data/`), rooted patterns (`/scripts/`), and negation (`!important.py`).

## SDK

```python
from csegraph import BenchmarkService, ContextService, GraphQueryService, IndexService, RefreshService

IndexService(".csegraph/index.db").index(".", profile="medium")
RefreshService(".csegraph/index.db").refresh(profile="medium")

context = ContextService(".csegraph/index.db").build_context(
    task="fix auth token refresh bug",
    target="refresh_token",
    profile="medium",
)

graph = GraphQueryService(".csegraph/index.db").neighborhood("refresh_token", depth=1)
benchmark = BenchmarkService(".csegraph/index.db").run(".", target="refresh_token")
```

## Context Output (v2)

Context responses include:

- `schema_version = "csegraph-context-v2"` (breaking schema change; v1 is no longer produced)
- `detail_level` (requested) and `returned_detail_level` (actual: auto may return minimal or standard)
- `minimal`: compact routing card with top 5 nodes, no source text; includes next_actions for expansion
- `standard`: working context with selected source text under token budget
- `full`: all nodes with explanations for each selection reason
- ranked `nodes` with paths, line ranges, reason tags, estimated tokens
- optional `source_text` (standard/full with selection heuristics)
- optional `explanation` (full or when --explain requested)
- `next_actions` list with deterministic suggestions (expand_context, inspect_graph, check_report)
- sufficiency metrics and thresholds

All detail levels return the same `nodes` array structure; they differ in what's populated (source_text, explanation) and what's included (routing vs. working context).

## Development

```bash
env/bin/python -m pytest tests/ -q
env/bin/python -m compileall -q csegraph_core packages/csegraph packages/csegraph-cli
env/bin/python -m csegraph_cli --help
```
