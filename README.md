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
csegraph index                    # Build the repository index (auto-postprocess: full)
csegraph index --postprocess minimal  # Index with FTS only (skip community detection)
csegraph index --postprocess none     # Index without postprocessing (fastest)
csegraph refresh                  # Incremental refresh for changed/deleted files
csegraph refresh --postprocess none   # Refresh without postprocessing
csegraph context "task"           # Retrieve context (detail_level=auto: minimal if sufficient, else standard)
csegraph context "task" --detail-level standard  # Request working context with source
csegraph context "task" --detail-level full --explain  # Full context with explanations
csegraph context "task" --target symbol --format markdown
csegraph status --verbose         # Graph health and staleness
csegraph postprocess              # Rebuild FTS and communities without re-parsing (level: full)
csegraph postprocess --level minimal  # FTS only, skip community detection
csegraph postprocess --level none     # Skip all postprocessing
csegraph inspect symbol --depth 1 # Graph neighborhood
csegraph path source target       # Shortest path between nodes
csegraph graph                    # Generate interactive HTML graph
csegraph tree                     # Generate interactive HTML file tree
csegraph communities              # Detect graph communities
csegraph report --json            # Structural report
csegraph hooks install            # Install git auto-refresh hooks
csegraph watch                    # Auto-refresh on file changes
csegraph detect-changes --base-ref main  # Risk-scored changed symbols
csegraph vulnerabilities              # Scan for security vulnerabilities
csegraph vulnerabilities --limit 10   # Limit results per severity level
csegraph architecture                 # Community summaries and architecture overview
csegraph architecture --limit 5       # Limit number of community summaries
csegraph benchmark --target symbol
csegraph serve                    # Start MCP stdio server (all tools)
csegraph serve --tools csegraph_minimal,csegraph_context  # Expose only selected tools
csegraph install                  # Configure local MCP client files
csegraph install --platform cursor --dry-run --json
csegraph install --instructions   # Generate CLAUDE.md, AGENTS.md, GEMINI.md, CODEX.md
csegraph install --hooks          # Install agent hooks (auto-refresh, status checks)
csegraph install --instructions --hooks  # Full agent onboarding
```

By default, the index is stored at `<repo>/.csegraph/index.db`.

Use `--profile small|medium|large` to trade retrieval breadth against speed and token budget. Use `csegraph.json`, `csegraph.toml`, or `--config` to tune thresholds without editing source.

AI assistants can call these MCP tools after `csegraph serve` is configured by the client. `csegraph install` writes stdio MCP configuration for supported clients; use `--platform codex|cursor|claude-code|gemini-cli|kiro|copilot` to target one client. Add `--instructions` to generate platform instruction files that tell agents to use csegraph first. Add `--hooks` to install agent hooks for automatic index refresh after file edits.

| Tool | Description | Key args |
|---|---|---|
| `csegraph_index` | Build a repository SQLite graph index. | `repo`, `profile`, `db`, `postprocess_level` |
| `csegraph_refresh` | Refresh changed/deleted files in an existing index. | `repo`, `profile`, `db`, `postprocess_level` |
| `csegraph_minimal` | Compact routing card (call first): summary + top-degree entities + task-routed next-tool suggestions. | `repo`, `task`, `db` |
| `csegraph_context` | Retrieve compact task-specific context. | `repo`, `task`, `target`, `profile`, `detail_level`, `include_source`, `max_tokens`, `max_bytes`, `explain`, `db` |
| `csegraph_graph` | Inspect a graph neighborhood around a node. Hub-aware BFS suppresses expansion through high-degree utility nodes. | `repo`, `node`, `depth`, `detail_level`, `relations`, `max_bytes`, `db` |
| `csegraph_path` | Find the shortest path between two nodes. Hub-aware BFS via SQLite recursive CTE with relation filtering matching `csegraph_graph` behavior. | `repo`, `source`, `target`, `detail_level`, `relations`, `max_depth`, `max_bytes`, `db` |
| `csegraph_detect_changes` | Detect changed symbols between current state and a base git ref, score each by review risk (caller count, cross-community edges, test coverage). | `repo`, `base_ref`, `db` |
| `csegraph_test_gaps` | Analyze test coverage gaps — untested symbols ranked by hotspot score, per-community coverage. | `repo`, `limit`, `db` |
| `csegraph_review_questions` | Generate targeted review questions from change detection and graph structure. | `repo`, `base_ref`, `db` |
| `csegraph_review_eval` | Evaluate review intelligence precision/recall against ground-truth known-risky symbols. | `repo`, `ground_truth_ids`, `base_ref`, `risk_threshold`, `db` |
| `csegraph_vulnerabilities` | Scan for security vulnerabilities — dangerous calls, untested security code, hardcoded secrets, high-exposure sinks. | `repo`, `limit`, `db` |
| `csegraph_architecture` | Community summaries and architecture overview — auto-labeled communities, key symbols, coupling analysis. | `repo`, `limit`, `db` |

The MCP surface stays focused on context delivery to agents. Visualization, community detection, and structural reports remain available as local CLI commands (`csegraph graph|tree|communities|report`) for human inspection.

Note: `csegraph_context` supports both `max_tokens` (a soft budgeting hint used during retrieval to decide how much source material to include) and `max_bytes` (a hard ceiling enforced on the serialized JSON response; when exceeded the server drops `source_text`, then `explanation`, then trims `nodes`/`edges`).

### Response annotations

Every MCP response carries metadata that agents can use to triage and gate further calls:

| Field | Where | Meaning |
|---|---|---|
| `tools_already_called` | every response | Sorted list of tools called in this MCP session. Suggestions whose `tool` field is in this set are filtered out automatically. |
| `response_bytes` | every response | Exact serialized JSON size in bytes. |
| `byte_cap_applied`, `byte_cap`, `truncated_fields` | when `max_bytes` is set | Whether truncation kicked in and what was dropped. Drop order: `source_text` → `explanation` → trim `nodes` from the tail → trim `edges` from the tail. |
| `confidence_breakdown` | `csegraph_graph`, `csegraph_path`, `csegraph_context` | `{"EXTRACTED": N, "INFERRED": M, "AMBIGUOUS": K}` — edge-trust mix, surfaced even in `detail_level=minimal` where edges are dropped. |
| `hubs_skipped` | `csegraph_graph`, `csegraph_path` | Number of high-degree utility nodes BFS refused to expand through. |
| `relations_filter` | `csegraph_graph`, `csegraph_path` | Echo of the `relations` arg applied to traversal, for transparency. |
| `next_tool_suggestions`, `next_actions` | `csegraph_minimal`, `csegraph_context` | Routing recommendations, already filtered against `tools_already_called`. |

MCP prompts are workflow templates that clients may expose as slash commands.

| Prompt | Workflow |
|---|---|
| `csegraph-index` | Ask the agent to build the graph with `csegraph_index`. |
| `csegraph-refresh` | Ask the agent to refresh changed files with `csegraph_refresh`. |
| `csegraph-minimal` | Call `csegraph_minimal` first for a routing card. |
| `csegraph-context` | Retrieve task-specific context with `csegraph_context`. |
| `csegraph-detect-changes` | Detect changed symbols and score review risk. |
| `csegraph-test-gaps` | Identify untested symbols and coverage hotspots. |
| `csegraph-review-questions` | Generate review questions from change detection and graph structure. |
| `csegraph-review-eval` | Evaluate review intelligence against known-risky symbols. |
| `csegraph-review` | Review changes with change detection, context, and graph tools. |
| `csegraph-vulnerabilities` | Scan the codebase for security vulnerabilities using the dependency graph. |
| `csegraph-architecture` | Generate community summaries and architecture overview with coupling analysis. |
| `csegraph-pre-merge` | Run a pre-merge context and risk checklist. |

## .csegraphignore

Place a `.csegraphignore` file in the repository root to exclude files and directories from indexing. Supports a `.gitignore`-like subset: blank lines, `#` comments, glob patterns (`*.generated.py`), directory patterns (`data/`), rooted patterns (`/scripts/`), and negation (`!important.py`).

## SDK

```python
from csegraph import (
    BenchmarkService, ContextService, GraphQueryService,
    IndexService, MinimalService, RefreshService,
    StatusService, ReportService, PostprocessService,
)

IndexService(".csegraph/index.db").index(".", profile="medium")
RefreshService(".csegraph/index.db").refresh(profile="medium")

# Routing card (call first — ~150 tokens)
routing = MinimalService(".csegraph/index.db").build_minimal(task="fix auth bug")

# Task-specific context
context = ContextService(".csegraph/index.db").build_context(
    task="fix auth token refresh bug",
    target="refresh_token",
    profile="medium",
)

# Graph inspection
graph = GraphQueryService(".csegraph/index.db").neighborhood("refresh_token", depth=1)

# Other services
status = StatusService(".csegraph/index.db").status()
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
pytest                              # Full test suite (559 tests)
pytest tests/unit/                  # Unit tests only
pytest tests/integration/           # Integration tests only
pytest -x -q                        # Stop on first failure, quiet
python -m compileall -q csegraph_core packages/csegraph packages/csegraph-cli
csegraph --help
```
