# csegraph Command Reference

Runnable examples use the repository virtualenv. Installed users can replace
`env/bin/csegraph` with `csegraph`.

## Setup

```bash
env/bin/python -m pip install -e .
env/bin/csegraph install --platform auto
env/bin/csegraph install --platform codex --dry-run --json
env/bin/csegraph install --platform codex --no-hooks --no-instructions --no-gitignore
```

The editable install includes Python, JavaScript, and TypeScript grammars. Use
`env/bin/python -m pip install -e ".[all]"` when you want every optional
tree-sitter grammar, or install individual extras such as `.[go,rust]`.

Global `--verbose` and `--quiet` flags control diagnostic logging. Put them
before the subcommand, for example `env/bin/csegraph --verbose watch .`.

`install` configures MCP clients to launch `csegraph serve`, writes
platform-scoped agent guidance, and installs supported refresh/status lifecycle
hooks. It also adds generated setup paths and `.csegraph/` to `.gitignore` by
default. Use `--no-instructions`, `--no-hooks`, or `--no-gitignore` for a
narrow MCP-only setup. The legacy `--instructions` and `--hooks` flags are
still accepted when you want to force all supported instruction or hook targets.

Use `--platform codex|cursor|claude-code|gemini-cli|kiro|copilot` to target one
client. Install commands write repo-local client config by default, including
`.codex/`, `.cursor/`, `.gemini/`, `.kiro/`, `.vscode/`, and `.mcp.json`.
Codex hooks are written to `.codex/hooks.json` so they show up in Codex's Hooks
view after the project config layer is trusted. Review generated local setup
before sharing logs or issue reproductions, and do not commit it.

VS Code extension install and project setup live in
[`csegraph-vscode/README.md`](../csegraph-vscode/README.md).

## Index And Refresh

```bash
env/bin/csegraph index .
env/bin/csegraph index . --profile medium --postprocess full --json
env/bin/csegraph index . --include-root apps/api --include-root packages/shared --json
env/bin/csegraph refresh .
env/bin/csegraph refresh . --postprocess minimal --json
env/bin/csegraph postprocess . --level full --json
env/bin/csegraph watch .
env/bin/csegraph status . --verbose
```

Postprocess levels:

- `none`: parse/write only.
- `minimal`: rebuild FTS.
- `full`: rebuild FTS, resolver edges, and communities.

Default DB: `<repo>/.csegraph/index.db`.

For monorepos, repeat `--include-root` to limit indexing to selected repo-local
subtrees. Refresh reuses the indexed include roots unless new include roots are
provided.

Codex-safe temporary artifacts should live under `<repo>/.scratch/csegraph/`, not
OS temp directories such as `/tmp` or `/private/tmp`. Use that repo-local scratch
area for throwaway DBs, exports, and test fixtures, and clean up generated
artifacts before handoff.

## Retrieval

```bash
env/bin/csegraph context "fix auth token refresh" --target refresh_token --json
env/bin/csegraph context "explain the index pipeline" --detail-level standard --format markdown
env/bin/csegraph context "debug parser misses" --include-source always --max-tokens 6000 --explain --json
env/bin/csegraph inspect ContextService.build_context --depth 1 --relations calls,imports --json
env/bin/csegraph path IndexService.index ContextService.build_context --relations calls,imports --json
```

Context flags:

- `--detail-level auto|minimal|standard|full`
- `--include-source auto|always|never`
- `--target NODE_OR_SYMBOL_OR_PATH`
- `--max-tokens N`
- `--format json|markdown`
- `--profile small|medium|large`

Graph/path flags:

- `--detail-level minimal|standard`
- `--relations calls,imports,tested_by`

JSON responses include `repo_root`; per-node `path` fields are repo-relative to
that root. Harnesses that need absolute file names should join `repo_root` and
`path` instead of expecting repeated absolute paths in every node.

## MCP

```bash
env/bin/csegraph serve
env/bin/csegraph serve --tools core
env/bin/csegraph serve --tools csegraph_minimal,csegraph_context
```

MCP exposes six tools only:

| Tool | Description | Key args |
|---|---|---|
| `csegraph_index` | Build a repository SQLite graph index. | `repo`, `profile`, `db`, `postprocess_level` |
| `csegraph_refresh` | Refresh changed/deleted files in an existing index. | `repo`, `profile`, `db`, `postprocess_level` |
| `csegraph_minimal` | Compact routing card: summary, top-degree entities, and task-routed next-tool suggestions. | `repo`, `task`, `db` |
| `csegraph_context` | Retrieve compact task-specific context. | `repo`, `task`, `target`, `profile`, `detail_level`, `include_source`, `max_tokens`, `max_bytes`, `explain`, `db` |
| `csegraph_graph` | Inspect a graph neighborhood around a node. Hub-aware BFS suppresses expansion through high-degree utility nodes. | `repo`, `node`, `depth`, `detail_level`, `relations`, `max_bytes`, `db` |
| `csegraph_path` | Find the shortest path between two nodes. Hub-aware BFS uses relation filtering matching `csegraph_graph` behavior. | `repo`, `source`, `target`, `detail_level`, `relations`, `max_depth`, `max_bytes`, `db` |

Agents should use minimal/context first and inspect/path only when the returned
next action calls for it.

`csegraph serve --tools` accepts `core` or a comma-separated subset of the six
core tool names. The MCP surface does not expose CLI operations such as
`analyze`, `export`, `registry`, or `daemon`, and it does not expose
maintainer-only benchmark/eval tools.

`csegraph_context` supports both `max_tokens`, a soft budgeting hint used during
retrieval to decide how much source material to include, and `max_bytes`, a hard
ceiling enforced on the serialized JSON response. When `max_bytes` is exceeded,
the server drops `source_text`, then `explanation`, then trims `nodes` and
`edges`.

### Response Annotations

Every MCP response carries metadata that agents can use to triage and gate
further calls:

| Field | Where | Meaning |
|---|---|---|
| `tools_already_called` | every response | Sorted list of tools called in this MCP session. Suggestions whose `tool` field is in this set are filtered out automatically. |
| `response_bytes` | every response | Exact serialized JSON size in bytes. |
| `byte_cap_applied`, `byte_cap`, `truncated_fields` | when `max_bytes` is set | Whether truncation kicked in and what was dropped. Drop order: `source_text`, `explanation`, trim `nodes` from the tail, trim `edges` from the tail. |
| `confidence_breakdown` | `csegraph_graph`, `csegraph_path`, `csegraph_context` | Edge-trust mix, surfaced even in `detail_level=minimal` where edges are dropped. |
| `hubs_skipped` | `csegraph_graph`, `csegraph_path` | Number of high-degree utility nodes BFS refused to expand through. |
| `relations_filter` | `csegraph_graph`, `csegraph_path` | Echo of the `relations` arg applied to traversal, for transparency. |
| `next_tool_suggestions`, `next_actions` | `csegraph_minimal`, `csegraph_context` | Routing recommendations, already filtered against `tools_already_called`. |

MCP prompts are workflow templates that clients expose as slash commands.

| Prompt | Workflow |
|---|---|
| `csegraph-index` | Build the graph with `csegraph_index`. |
| `csegraph-refresh` | Refresh changed files with `csegraph_refresh`. |
| `csegraph-minimal` | Routing card; call first. |
| `csegraph-context` | Task-specific context with `csegraph_context`. |
| `csegraph-debug-issue` | Debug workflow: minimal, context, optional graph. |
| `csegraph-review-changes` | Pre-commit review: refresh, minimal, context. |
| `csegraph-pre-merge-check` | Merge readiness: minimal, context, optional graph. |
| `csegraph-explore-architecture` | Architecture map: minimal, graph neighborhood. |
| `csegraph-onboard-developer` | Onboarding guide: minimal, context, graph. |

Project `.mcp.json` should use `env/bin/csegraph`, or run
`csegraph install --platform claude-code`, so Claude Code does not require a
global `csegraph` on PATH.

## Public Operations

These are public CLI commands, not MCP tools:

```bash
env/bin/csegraph analyze . --json
env/bin/csegraph export . --format html --output graph.html
env/bin/csegraph export . --format tree
env/bin/csegraph export . --format json --output graph.json
env/bin/csegraph export . --format graphml --output graph.graphml
env/bin/csegraph export . --format obsidian --output vault
env/bin/csegraph watch .
env/bin/csegraph registry register /path/to/repo --alias app
env/bin/csegraph registry list
env/bin/csegraph daemon start --alias app
env/bin/csegraph daemon status
```

Supported export formats:
- `html`: Generates an interactive web graph visualization featuring an electric blue theme, N-body repulsion physics, neighborhood isolation focus, and code summary tooltips.
- `tree`: Generates an interactive file tree visualization.
- `json`: Exports a portable JSON graph representation.
- `graphml`: Exports in standard GraphML format.
- `obsidian`: Exports markdown notes formatted as an Obsidian vault.


## Maintainer Tooling

Development analytics stay repo-local:

```bash
env/bin/python tools/csegraph_dev.py benchmark . --target symbol
env/bin/python tools/csegraph_dev.py benchmark . --corpus benchmarks/context_quality/csegraph_self.json --json
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

See [Token Reduction Benchmark](token-reduction-benchmark.md) for measured
context-size reductions from benchmark runs on this repository.

There is no `csegraph-dev` console script.

## Profiles And Config

Profiles trade breadth against token budget:

- `small`: narrow retrieval, fastest context.
- `medium`: default.
- `large`: wider graph expansion and more source.

Config files: `csegraph.json`, `csegraph.toml`, or `--config`.

Supported config keys in `csegraph.json` or `csegraph.toml` include:
- `profile`: Base profile name to load (`small`, `medium`, or `large`).
- `top_k`: Number of lexical query candidates to retrieve.
- `graph_radius`: Radius of the neighborhood expansion.
- `context_budget`: Maximum budget for the context package in tokens.
- `raw_code_budget`: Token budget limit for raw code source nodes.
- `max_bytes`: Hard ceiling on the serialized JSON response size.
- `dep_threshold`, `entity_threshold`, `semantic_threshold`, `semantic_threshold_relaxed`, `confidence_threshold`: Various retrieval filtering thresholds.

All keys must use underscore notation matching these Python/JSON property names. Unknown keys raise `ValueError`.

## Ignore Policy

Use `.csegraphignore` for CseGraph-specific exclusions. It supports comments,
globs, rooted patterns, directory patterns, basename matching, and `!`
negation.

Discovery order: `git ls-files` (staged and committed, submodules on by default),
then `svn list -R` for SVN working copies, then a directory walk. Untracked git
files are not indexed until `git add`. Use `.csegraphignore` to exclude index entries
from agent context.

| Variable | Effect |
|----------|--------|
| `CSEGRAPH_RECURSE_SUBMODULES` | `0` / `false` disables submodule paths in discovery; default is on |
| `CSEGRAPH_HEALTH_STALE_HOURS` | Hours before index is flagged stale in `status` / minimal (default `24`) |
| `CSEGRAPH_HEALTH_THIN_FILES` | File count below which index is “thin” (default `3`) |

`csegraph index` and `csegraph refresh` accept repeatable `--exclude PATTERN` for
runtime gitignore-style rules (in addition to `.csegraphignore`).

`csegraph status` and `csegraph_minimal` include `index_health`: `verdict` (`ok`, `thin`, `stale`, `large`, `errors`, `rebuild`), `summary`, `metrics`, and `hints` for agents.

## SDK

```python
from csegraph import (
    ContextService,
    GraphQueryService,
    IndexService,
    MinimalService,
    PostprocessService,
    RefreshService,
    StatusService,
)

IndexService(".csegraph/index.db").index(".", profile="medium")
RefreshService(".csegraph/index.db").refresh(profile="medium")

routing = MinimalService(".csegraph/index.db").first(task="fix auth bug")

context = ContextService(".csegraph/index.db").build_context(
    task="fix auth token refresh bug",
    target="refresh_token",
    profile="medium",
)

graph = GraphQueryService(".csegraph/index.db").neighborhood(
    "refresh_token",
    depth=1,
)

status = StatusService(".csegraph/index.db").status()
PostprocessService(".csegraph/index.db").postprocess(level="minimal")
```

Async applications can use thread-backed async facades for the main SDK
services:

```python
from csegraph import AsyncContextService, AsyncIndexService

await AsyncIndexService(".csegraph/index.db").index(".", profile="medium")
context = await AsyncContextService(".csegraph/index.db").build_context(
    task="fix auth token refresh bug",
    target="refresh_token",
    profile="medium",
)
```

Custom parser integrations can register process-local parsers without forking
CseGraph:

```python
from csegraph import BaseParser, register_parser

class MyParser(BaseParser):
    language = "my_language"
    extensions = (".mine",)
    # implement parse(), module_name_from_relpath(), resolve_local_import()

register_parser(MyParser())
```

## Context Output

Context responses include:

- `schema_version = "csegraph-context-v2"`; v1 is no longer produced.
- `detail_level` and `returned_detail_level`; `auto` may return minimal or standard.
- `minimal`: compact routing card with top nodes, no source text, and next actions.
- `standard`: working context with selected source text under token budget.
- `full`: all nodes with explanations for each selection reason.
- ranked `nodes` with paths, line ranges, reason tags, and estimated tokens.
- optional `source_text` in standard/full responses.
- optional `explanation` in full responses or when `--explain` is requested.
- `next_actions` with deterministic suggestions.
- sufficiency metrics and thresholds.

All detail levels return the same `nodes` array structure. They differ in which
fields are populated and whether the response is a routing card or working
context.
