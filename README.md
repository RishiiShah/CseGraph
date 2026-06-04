# csegraph

CseGraph is a **context engine for coding agents**. Its only job is to hand an agent the accurate, minimal slice of code context needed to make a correct retrieval or edit, so the agent spends fewer tokens and skips tool calls it would otherwise make (broad grep, full-file read, repeated lookups).

It indexes source code into a SQLite-backed dependency graph, then returns compact, task-specific context bundles before an agent edits. Next-upgrade learning notes: [`learn.md`](learn.md).

The product loop is:

```text
index -> refresh -> context -> optional inspect/path/analyze
```

Use csegraph when you want an agent to see the target code, direct dependencies, imports, nearby tests, and a short explanation of why each node was selected without repeatedly scanning the repository.

## Package Layout

| Package | Location | Purpose |
|---|---|---|
| `csegraph` | repo root | One Python distribution containing the public CLI, MCP server, SDK facade, and private engine internals. |
| `csegraph-vscode` | `csegraph-vscode/` | VS Code extension source: commands, status bar, auto-refresh on save, right-click inspect. See [extension README](csegraph-vscode/README.md) for CLI discovery and troubleshooting. |

Public Python imports use `csegraph`. Internal implementation modules live under `csegraph._core` and `csegraph._cli`; they are not documented as public API.

## Install From Source

```bash
env/bin/pip install -e .
```

`requirements.txt` contains the same product-only editable install.

This repository is source-first. The public project is distributed as one Python
package and the VS Code extension source; generated binaries, local graph
databases, build outputs, and dashboard artifacts are not committed.

## Project Hygiene

- Security policy: [SECURITY.md](SECURITY.md)
- Contribution guide: [CONTRIBUTING.md](CONTRIBUTING.md)
- Release checklist: [RELEASE.md](RELEASE.md)
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Maintainer command reference: [docs/csegraph.md](docs/csegraph.md)
- Architecture reference: [docs/architecture.md](docs/architecture.md)

## Base Commands

```bash
csegraph install                  # Configure local MCP/client files
csegraph install --platform cursor --dry-run --json
csegraph install --platform vscode    # Write .vscode/ settings, tasks, extension recommendation
csegraph install --instructions   # Generate CLAUDE.md, AGENTS.md, GEMINI.md, CODEX.md
csegraph install --hooks          # Install agent hooks (auto-refresh, status checks)
csegraph index                    # Build the repository index (auto-postprocess: full)
csegraph index --postprocess minimal  # Index with FTS only (skip community detection)
csegraph index --postprocess none     # Index without postprocessing (fastest)
csegraph refresh                  # Incremental refresh for changed/deleted files
csegraph refresh --postprocess none   # Refresh without postprocessing
csegraph postprocess              # Rebuild FTS and communities without re-parsing (level: full)
csegraph postprocess --level minimal  # FTS only, skip community detection
csegraph watch                    # Auto-refresh on file changes
csegraph status --verbose         # Graph health and staleness
csegraph serve                    # Start MCP stdio server (core tools only)
csegraph serve --tools core       # Explicitly expose the six core tools
csegraph serve --tools csegraph_minimal,csegraph_context  # Expose only selected tools
csegraph context "task"           # Retrieve context (detail_level=auto: minimal if sufficient, else standard)
csegraph context "task" --detail-level standard  # Request working context with source
csegraph context "task" --detail-level full --explain  # Full context with explanations
csegraph context "task" --target symbol --format markdown
csegraph inspect symbol --depth 1 # Graph neighborhood
csegraph path source target       # Shortest path between nodes
csegraph analyze                  # Ranked local diagnostics summary
csegraph export --format html     # Interactive HTML graph
csegraph export --format tree     # Interactive HTML file tree
csegraph export --format json -o out.json  # Portable JSON graph dump
csegraph registry register /path/to/repo --alias myapp
csegraph registry list
csegraph daemon start --alias myapp
csegraph daemon status
```

By default, the index is stored at `<repo>/.csegraph/index.db`.

Codex-safe temporary artifacts should live under `<repo>/.scratch/csegraph/`, not OS temp directories such as `/tmp` or `/private/tmp`. Use that repo-local scratch area for throwaway DBs, exports, and test fixtures, and clean up generated artifacts before handoff.

Use `--profile small|medium|large` to trade retrieval breadth against speed and token budget. Use `csegraph.json`, `csegraph.toml`, or `--config` to tune thresholds without editing source.

AI assistants can call these MCP tools after `csegraph serve` is configured by the client. `csegraph install` writes stdio MCP configuration for supported clients; use `--platform codex|cursor|claude-code|gemini-cli|kiro|copilot|vscode` to target one client. Use `--platform vscode` to write VS Code settings, tasks, and extension recommendations for the csegraph-vscode extension. Add `--instructions` to generate platform instruction files that tell agents to use csegraph first. Add `--hooks` to install agent hooks for automatic index refresh after file edits.

`csegraph serve --tools` accepts `core` or a comma-separated subset of the six core tool names. The MCP surface is explicitly limited to these six tools; it does not expose CLI operations such as `analyze`, `export`, `registry`, or `daemon`, and it does not expose maintainer-only benchmark/eval tools.

| Tool | Description | Key args |
|---|---|---|
| `csegraph_index` | Build a repository SQLite graph index. | `repo`, `profile`, `db`, `postprocess_level` |
| `csegraph_refresh` | Refresh changed/deleted files in an existing index. | `repo`, `profile`, `db`, `postprocess_level` |
| `csegraph_minimal` | Compact routing card (call first): summary + top-degree entities + task-routed next-tool suggestions. | `repo`, `task`, `db` |
| `csegraph_context` | Retrieve compact task-specific context. | `repo`, `task`, `target`, `profile`, `detail_level`, `include_source`, `max_tokens`, `max_bytes`, `explain`, `db` |
| `csegraph_graph` | Inspect a graph neighborhood around a node. Hub-aware BFS suppresses expansion through high-degree utility nodes. | `repo`, `node`, `depth`, `detail_level`, `relations`, `max_bytes`, `db` |
| `csegraph_path` | Find the shortest path between two nodes. Hub-aware BFS via SQLite recursive CTE with relation filtering matching `csegraph_graph` behavior. | `repo`, `source`, `target`, `detail_level`, `relations`, `max_depth`, `max_bytes`, `db` |

The MCP surface stays focused on the six core context-engine tools for index, refresh, retrieval, and inspection. Public operational commands such as `analyze`, `export`, `registry`, and `daemon` remain local CLI commands.

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

MCP prompts are workflow templates that clients expose as slash commands (e.g. `/csegraph:csegraph-debug-issue` in Claude Code).

| Prompt | Workflow |
|---|---|
| `csegraph-index` | Build the graph with `csegraph_index`. |
| `csegraph-refresh` | Refresh changed files with `csegraph_refresh`. |
| `csegraph-minimal` | Routing card (~150 tokens); call first. |
| `csegraph-context` | Task-specific context with `csegraph_context`. |
| `csegraph-debug-issue` | Debug workflow: minimal → context → optional graph. |
| `csegraph-review-changes` | Pre-commit review: refresh → minimal → context (CLI `analyze` optional). |
| `csegraph-pre-merge-check` | Merge readiness: minimal → context → optional graph. |
| `csegraph-explore-architecture` | Architecture map: minimal → graph neighborhood. |
| `csegraph-onboard-developer` | Onboarding guide: minimal → context → graph. |

**MCP connection:** Project `.mcp.json` should use `env/bin/csegraph` (or run `csegraph install --platform claude-code`) so Claude Code does not require a global `csegraph` on PATH.

## Public Operations

These commands remain available to users as local CLI operations. They are not MCP tools, and the slim SDK facade stays focused on indexing, refresh, context, graph inspection, status, and postprocess:

```bash
csegraph analyze                  # One ranked diagnostics summary
csegraph export --format html     # Generate interactive HTML graph
csegraph export --format tree     # Generate interactive HTML file tree
csegraph export --format graphml  # Portable GraphML export
csegraph export --format obsidian # Obsidian vault export
csegraph watch                    # Auto-refresh on file changes
csegraph registry register /path/to/repo --alias myapp
csegraph daemon start --alias myapp
```

## Maintainer Tooling

CseGraph development analytics and experimental commands are repo-local only:

```bash
env/bin/python tools/csegraph_dev.py benchmark . --target symbol
env/bin/python tools/csegraph_dev.py detect-changes . --base-ref HEAD~1 --json
env/bin/python tools/csegraph_dev.py test-gaps . --json
env/bin/python tools/csegraph_dev.py architecture . --json
env/bin/python tools/csegraph_dev.py flows . --json
env/bin/python tools/csegraph_dev.py vulnerabilities . --json
env/bin/python tools/csegraph_dev.py communities . --json
env/bin/python tools/csegraph_dev.py resolvers . --json
env/bin/python tools/csegraph_dev.py review-eval . --ground-truth ids.json
env/bin/python tools/csegraph_dev.py review-questions . --json
env/bin/python tools/csegraph_dev.py report . --json
env/bin/python tools/csegraph_dev.py embeddings status .
```

There is no packaged `csegraph-dev` console script. These maintainer-only benchmark, eval, and development-analytics surfaces stay repo-local behind `tools/csegraph_dev.py` rather than being part of the public CLI, SDK, or MCP surface.

## .csegraphignore

Place a `.csegraphignore` file in the repository root to exclude files and directories from indexing. Supports a `.gitignore`-like subset: blank lines, `#` comments, glob patterns (`*.generated.py`), directory patterns (`data/`), rooted patterns (`/scripts/`), and negation (`!important.py`).

## SDK

```python
from csegraph import (
    ContextService, GraphQueryService, IndexService, MinimalService,
    PostprocessService, RefreshService, StatusService,
)

IndexService(".csegraph/index.db").index(".", profile="medium")
RefreshService(".csegraph/index.db").refresh(profile="medium")

# Routing card (call first — ~150 tokens)
routing = MinimalService(".csegraph/index.db").first(task="fix auth bug")

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
PostprocessService(".csegraph/index.db").postprocess(level="minimal")
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
- `next_actions` list with deterministic suggestions (expand_context, inspect_graph)
- sufficiency metrics and thresholds

All detail levels return the same `nodes` array structure; they differ in what's populated (source_text, explanation) and what's included (routing vs. working context).

## Development

```bash
pytest                              # Full test suite
pytest tests/unit/                  # Unit tests only
pytest tests/integration/           # Integration tests only
pytest -x -q                        # Stop on first failure, quiet
python -m compileall -q csegraph tools csegraph-vscode
csegraph --help
```

## License

CseGraph is released under the MIT License. See [LICENSE](LICENSE).
