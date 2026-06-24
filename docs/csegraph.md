# csegraph Command Reference

The examples below assume CseGraph is installed and the `csegraph` command is
available on your PATH.

## Setup

```bash
pipx install csegraph
csegraph install --platform auto
csegraph install --platform codex --dry-run --json
csegraph install --platform codex --no-hooks --no-instructions --no-gitignore
```

Current package release: `1.8.0`. To install it exactly:

```bash
pipx install csegraph==1.8.0
```

The base package includes Python, JavaScript, and TypeScript grammars. Install
individual extras such as `csegraph[go,rust]`, or use `csegraph[all]` for every
optional Tree-sitter grammar. See the
[platform-specific installation guide](https://github.com/RishiiShah/CseGraph#install)
for macOS, Linux, and Windows.

Global `--verbose` and `--quiet` flags control diagnostic logging. Put them
before the subcommand, for example `csegraph --verbose watch .`.

`install` configures MCP clients to launch the real CseGraph CLI MCP server as
`csegraph serve --repo <absolute repo> --platform <client>`, writes
platform-scoped agent guidance, installs supported refresh/status lifecycle
hooks, verifies the generated MCP tool surface by default, and adds generated
setup paths plus `.csegraph/` to `.gitignore`. Use `--no-instructions`,
`--no-hooks`, `--no-gitignore`, or `--no-verify` for a narrower MCP-only setup.
The legacy `--instructions` and `--hooks` flags are still accepted when you want
to force all supported instruction or hook targets.

Generated MCP configs are intended to be ready to use on macOS, Linux, and
Windows. The installer resolves the native absolute CLI path, including Windows
virtualenv/pipx launchers such as `Scripts\csegraph.exe`, `Scripts\csegraph.cmd`,
or `Scripts\csegraph.bat`, and Python user-install launchers such as
`%APPDATA%\Python\PythonXY\Scripts\csegraph.exe` or `~/.local/bin/csegraph`.
It then writes the same `serve --repo <absolute repo> --platform <client>`
contract for every supported host. If setup looks wrong, run `doctor`; do not
hand-edit generated `mcp.json` files as the normal path.

Use
`--platform codex|cursor|claude-code|gemini-cli|kiro|antigravity-cli|antigravity-ide|copilot|vscode`
to target one client. Install commands write repo-local client config by
default, including `.codex/`, `.cursor/`, `.gemini/`, `.kiro/`, `.agents/`,
`.vscode/`, and `.mcp.json`. `antigravity-ide` is explicit opt-in because it
writes user-global Antigravity IDE MCP config under `~/.gemini/config/`.
Codex hooks are written to `.codex/hooks.json` so they show up in Codex's Hooks
view after the project config layer is trusted. Review generated local setup
before sharing logs or issue reproductions, and do not commit it.

Use `doctor` to distinguish generated config from real protocol and host use:

```bash
csegraph doctor . --platform auto --json
csegraph doctor . --platform codex --json
csegraph doctor . --platform cursor --require-observed-call --json
```

`doctor --platform auto` inspects every project-scoped platform config. It
does not silently inspect or write global Antigravity IDE config; use
`--platform antigravity-ide` when you want that explicit global check.
After installation, open the current client's MCP/tools settings and enable or
approve the `csegraph` server until the six CseGraph tools are visible. The
local `.csegraph` index is shared, but host access is not: a Cursor config or
observed Cursor tool call does not count as Codex, Claude Code, Gemini CLI,
Kiro, Copilot, or Antigravity setup. Agents should not query
`.csegraph/index.db` directly or use CLI context commands as a substitute for
that platform's MCP server.

Doctor output separates setup layers:

- `config_present`: the platform config contains a `csegraph` server entry.
- `contract_valid`: the entry uses a native absolute `csegraph` executable and
  `["serve", "--repo", "<absolute repo>", "--platform", "<client>"]`, with
  required `cwd` where applicable.
- `protocol_verified`: the generated command initialized as an MCP server and
  advertised the six canonical CseGraph tools.
- `observed_call`: a real host for that same platform called one of those tools
  in this repo.

VS Code extension install and project setup live in the
[extension README](https://github.com/RishiiShah/CseGraph/tree/main/csegraph-vscode#readme).

## Index And Refresh

```bash
csegraph index .
csegraph index . --profile medium --postprocess full --json
csegraph index . --include-root apps/api --include-root packages/shared --json
csegraph refresh .
csegraph refresh . --postprocess minimal --json
csegraph postprocess . --level full --json
csegraph watch .
csegraph status . --verbose
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
csegraph context "fix auth token refresh" --target refresh_token --json
csegraph context "explain the index pipeline" --detail-level standard --format markdown
csegraph context "debug parser misses" --include-source always --max-tokens 6000 --explain --json
csegraph inspect ContextService.build_context --depth 1 --relations calls,imports --json
csegraph path IndexService.index ContextService.build_context --relations calls,imports --json
```

Context flags:

- `--detail-level auto|minimal|standard|full`
- `--include-source auto|always|never`
- `--target NODE_OR_SYMBOL_OR_PATH`
- `--max-tokens N`
- `--format json|markdown`
- `--profile auto|small|medium|large`

Graph/path flags:

- `--detail-level minimal|standard`
- `--relations calls,imports,tested_by`

JSON responses include `repo_root`; per-node `path` fields are repo-relative to
that root. Harnesses that need absolute file names should join `repo_root` and
`path` instead of expecting repeated absolute paths in every node.

## MCP

```bash
csegraph serve
csegraph serve --repo /path/to/repo
csegraph serve --repo /path/to/repo --platform codex
csegraph serve --tools core
csegraph serve --tools csegraph_minimal,csegraph_context
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

For MCP tools, `profile` accepts `auto`, `small`, `medium`, or `large`; omitted
profile arguments default to `auto`.

`csegraph serve --tools` accepts `core` or a comma-separated subset of the six
core tool names. The MCP surface does not expose CLI operations such as
`analyze`, `export`, `registry`, or `daemon`, and it does not expose
maintainer-only benchmark/eval tools.

## LSP

```bash
csegraph lsp --repo .
csegraph lsp . --db .csegraph/index.db
```

The LSP server speaks JSON-RPC over stdio for editor integrations. It advertises
document symbols for files already present in the SQLite index, so run
`csegraph index` or `csegraph refresh` before launching an editor client.

`csegraph_context` supports both `max_tokens`, a soft budgeting hint used during
retrieval to decide how much source material to include, and `max_bytes`, a hard
ceiling enforced on the serialized JSON response. When `max_bytes` is exceeded,
the server drops symbol `source_text`, then `explanation`, then
`import_preludes`, `relationships[].occurrences[].snippet`, `relationships`, and finally
`symbols` from the tail. File nodes never materialize whole-file source text.

### Response Annotations

Every MCP response carries metadata that agents can use to triage and gate
further calls:

| Field | Where | Meaning |
|---|---|---|
| `tools_already_called` | every response | Sorted list of tools called in this MCP session. Suggestions whose `tool` field is in this set are filtered out automatically. |
| `trust` | every MCP JSON response | Local trust metadata such as bound repo, effective repo, tool name, and index-health verdict when available. |
| `token_usage` | `csegraph_context` | Estimated context tokens used, indexed-corpus baseline tokens, saved tokens, and reduction ratio using a `chars/4` proxy. |
| `response_bytes` | every response | Exact serialized JSON size in bytes. |
| `byte_cap_applied`, `byte_cap`, `truncated_fields` | when `max_bytes` is set | Whether truncation kicked in and what was dropped. Context drop order: symbol `source_text`, `explanation`, `import_preludes`, `relationships[].occurrences[].snippet`, `relationships`, `symbols`. |
| `confidence_breakdown` | `csegraph_graph`, `csegraph_path`, `csegraph_context` | Edge-trust mix, surfaced even in `detail_level=minimal` where edges are dropped. |
| `hubs_skipped` | `csegraph_graph`, `csegraph_path` | Number of high-degree utility nodes BFS refused to expand through. |
| `relations_filter` | `csegraph_graph`, `csegraph_path` | Echo of the `relations` arg applied to traversal, for transparency. |
| `next_tool_suggestions`, `next_actions` | `csegraph_minimal`, `csegraph_context` | Routing recommendations, already filtered against `tools_already_called`. |
| `target.confidence`, `target.score_margin`, `sufficiency.verdict` | `csegraph_context` | Target-resolution and sufficiency trust signals. Low-confidence inferred targets are marked not sufficient instead of silently passing. |

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

Run `csegraph install --platform claude-code` to generate the project
`.mcp.json` configuration.

## Public Operations

These are public CLI commands, not MCP tools:

```bash
csegraph analyze . --json
csegraph export . --format html --output graph.html
csegraph export . --format tree
csegraph export . --format json --output graph.json
csegraph export . --format graphml --output graph.graphml
csegraph export . --format obsidian --output vault
csegraph doctor . --platform codex --json
csegraph watch .
csegraph registry register /path/to/repo --alias app
csegraph registry list
csegraph daemon start --alias app
csegraph daemon status
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

Native MCP benchmarks use sandbox workloads and the stdio transport:

```bash
env/bin/python tools/cross_repo_benchmark.py
env/bin/python tools/check_benchmark_regression.py --repo .
env/bin/python tools/run_full_mcp_benchmark.py
```

`tools/csegraph_dev.py benchmark` is still available for maintainer-only
SDK/internal service diagnostics, but those results should be labeled as
internal SDK benchmarks rather than native MCP agent benchmarks.

See [Agent Context Benchmarks](benchmarks.md) for measured context-size
reductions from sandbox repositories.
The benchmark corpus accepts file/symbol hits plus v3 evidence checks such as
`expected_relationships`, `expected_occurrence_snippets`,
`expected_import_preludes`, and `forbidden_source_patterns`.

There is no `csegraph-dev` console script.

## Profiles And Config

Profiles trade breadth against token budget:

- `auto`: resolves to `small`, `medium`, or `large` from repository size.
- `small`: narrow retrieval, fastest context.
- `medium`: middle retrieval breadth.
- `large`: wider graph expansion and more source.

Config files: `csegraph.json`, `csegraph.toml`, or `--config`.

Supported config keys in `csegraph.json` or `csegraph.toml` include:
- `profile`: Base profile name to load (`auto`, `small`, `medium`, or `large`).
- `top_k`: Number of lexical query candidates to retrieve.
- `graph_radius`: Radius of the neighborhood expansion.
- `context_budget`: Maximum budget for the context package in tokens.
- `raw_code_budget`: Token budget limit for raw code source nodes.
- `max_bytes`: Hard ceiling on the serialized JSON response size.
- `dep_threshold`, `entity_threshold`, `semantic_threshold`, `semantic_threshold_relaxed`, `confidence_threshold`: Retrieval filtering thresholds. `semantic_threshold_relaxed` is the active semantic-overlap floor once dependency and entity coverage pass; set it to `0.0` to disable that relaxed semantic gate.

All keys must use underscore notation matching these Python/JSON property names. Unknown keys raise `ValueError`.

## Ignore Policy

Use `.csegraphignore` for CseGraph-specific exclusions. It supports comments,
globs, rooted patterns, directory patterns, basename matching, and `!`
negation.

Discovery order: `git ls-files` (staged and committed, submodules on by default),
then `svn list -R` as a backup for SVN working copies, then a directory walk.
Untracked git and SVN files are not indexed until their VCS tracks them. Use
`.csegraphignore` to exclude index entries from agent context.

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

- `schema_version = "csegraph-context-v3"`.
- `request.detail_level` and `request.returned_detail_level`; `auto` may return minimal or standard.
- `target` with the resolved id, kind, path, line range, and ambiguity candidates.
- ranked `symbols` with paths, line ranges, reason tags, summaries, and estimated tokens.
- `relationships` for selected calls, callers, imports, inheritance, decorators, and tests.
  Relationships may include bounded `occurrences` with path, line range,
  enclosing symbol, name, kind, optional metadata, and optional callsite/import
  snippet.
  Default `confidence=1.0`, `confidence_tier=EXTRACTED`, and redundant endpoint
  paths are omitted from serialized context relationships.
- `import_preludes` containing import-only snippets for files that contain selected symbols.
- `minimal`: compact symbol-neighborhood card, no source text, and next actions.
- `standard`: selected symbol source slices plus relationships and import preludes.
- `full`: broader selected symbol slices with explanations for each selection reason.
- optional symbol `source_text` in standard/full responses; whole-file source is never returned.
- `source_omitted_reason` on symbols without source, for example
  `minimal_detail`, `source_policy_never`, `auto_source_budget`, or
  `token_budget`.
- optional `explanation` in full responses or when `--explain` is requested.
- `next_actions` with deterministic suggestions.
- sufficiency metrics and thresholds.

All detail levels return the same v3 top-level structure. They differ in which
symbol fields are populated and whether the response is a routing card or
working context.
