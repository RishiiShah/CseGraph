# Architecture

Short maintainer reference. User commands live in `docs/csegraph.md`; release
notes and changelog live at the repository root.

## Shape

```text
repository -> file discovery -> tree-sitter parsers -> SQLite graph
           -> postprocess -> minimal/context/graph/path retrieval
```

CseGraph is local-first and does not execute indexed code. The Python
distribution is one package, `csegraph`; implementation modules are private
under `csegraph._core` and `csegraph._cli`.

## Product Boundary

CseGraph's core job is to give coding agents the smallest accurate task context
needed for a retrieval, review, or edit. New features should preserve that job:

- Prefer features that improve indexing, refresh, retrieval quality, context
  sufficiency, response size, or agent/editor ergonomics around the context loop.
- Keep the MCP surface limited to the six-tool context loop: index, refresh,
  minimal, context, graph, and path.
- Keep broad diagnostics, experiments, review intelligence, benchmark tools,
  security scans, and embedding experiments behind repo-local maintainer tooling
  unless they directly route users back to `context` or `inspect`.
- Do not turn the public product into a general static analyzer, security
  scanner, observability platform, or project-management dashboard.
- When adding a public command, classify it as core context, support, or a
  narrow diagnostic bridge, and update the product-boundary tests deliberately.

## Main Modules

| Module | Responsibility |
|---|---|
| `csegraph._core.ignore` | Git-aware `.gitignore` + `.csegraphignore` policy. |
| `csegraph._core.languages` | Parser registry and tree-sitter language parsers. |
| `csegraph._core.index` | Full index, incremental refresh, cache, SQLite writes. |
| `csegraph._core.postprocess` | FTS rebuild, resolver edges, communities. |
| `csegraph._core.retrieval` | Minimal card and task-specific context selection. |
| `csegraph._core.graph` | Neighborhood, path, diagnostics, exports, private analytics. |
| `csegraph._core.server` | MCP stdio server with six core tools. |
| `csegraph._cli` | Public `csegraph` command implementation. |
| `csegraph-vscode/` | Thin VS Code UI over the public CLI. |

## Discovery

In git repositories, discovery uses `git ls-files` (the index: committed and
staged paths), with `--recurse-submodules` enabled by default. Untracked local
trees (for example local draft directories or notes) are not indexed until `git add`. That
keeps agent context aligned with what the developer has chosen to stage and avoids
half-baked code polluting retrieval.

- Set `CSEGRAPH_RECURSE_SUBMODULES=0` to skip submodule file paths (large vendor
  submodules).
- `.csegraphignore` excludes paths from the `git ls-files` candidate set.
- Force-added index entries (`git add -f`) remain candidates even when they match
  `.gitignore`.
- Without git, SVN working copies use `svn list -R` as a backup discovery path
  (versioned paths only).
- With neither git nor SVN, discovery falls back to a bounded directory walk honoring
  `.gitignore` and `.csegraphignore`.
- parser-specific excluded directories are applied after parser selection, so
  one language parser cannot hide another language's package files.

`csegraph watch` reloads the index path set on each debounced batch and only
refreshes discoverable paths.

`StatusService` and `MinimalService` run `corpus_health.assess_index_health` so agents
see `index_health.verdict` and hints (thin / stale / large / parse errors) without
extra MCP tools.

## Index Pipeline

1. Discover supported files using `IgnoreFilter`.
2. Hash files and reuse cached parse output when content is unchanged.
3. Dispatch by extension to the selected parser.
4. Store file/symbol nodes and extracted edges in SQLite.
5. Run postprocess unless `--postprocess none` was requested.

Postprocess levels:

- `none`: skip FTS, resolvers, and communities.
- `minimal`: rebuild FTS.
- `full`: rebuild FTS, resolver edges, and communities.

## SQLite Schema

Schema version: `csegraph-sqlite-v6` (`PRAGMA user_version = 6`).

CseGraph is still beta, so v6 is a breaking reset. Older SQLite indexes are not
migrated on ordinary access; readers fail fast with `UnsupportedSchemaError` and
an instruction to rerun `csegraph index <repo>`. The indexing path may reset an
old beta database before rebuilding it from source.

Core tables:

- `metadata`
- `nodes`
- `edges`
- `files`
- `symbols`
- `relationships`
- `imports`
- `symbol_references`
- `summaries`
- `lexical_index` (FTS5)
- `embedding_cache`
- `retrieval_runs`
- `retrieval_context`

Node types include repository, folder, file, class, function, method, test, and
import. Edge relations include contains, imports, calls, inherits, decorates,
and tested_by. Node IDs are opaque strings produced by `csegraph._core.core.ids`;
do not parse IDs in callers.

## Retrieval Pipeline

`ContextService.build_context()`:

1. Resolve an optional target from node ID, file path, or symbol name.
2. Retrieve lexical candidates using FTS5 plus exact-name and path boosts.
3. Add target boost when a target resolves.
4. Expand through graph relations with weighted BFS.
5. Select a symbol neighborhood: target symbols, direct callees, direct
   callers, same-file relevant symbols, imported-file symbols, then lexical or
   semantic fill within the profile budget.
6. Compute sufficiency metrics.
7. Package selected `symbols`, relationship directions, and import-only
   preludes. File nodes stay metadata-only and never emit whole-file source.
8. Materialize source slices for selected symbols depending on detail level and
   `include_source`.
9. Enforce token and byte budgets.

Retrieval outputs keep `path` fields repo-relative and include `repo_root` once
at the response root. Callers should combine those values when absolute paths
are required.

Detail levels:

- `minimal`: compact symbol-neighborhood routing card.
- `standard`: selected symbol source, relationships, and import preludes.
- `full`: selected symbol source plus explanations.
- `auto`: starts compact and escalates only when sufficiency requires it.

## Graph Queries

`GraphQueryService.neighborhood()` and path lookup use hub-aware traversal.
High-degree utility nodes are not expanded unless they are the explicit target.
`relations` and `confidence_tiers` are pre-traversal filters, not just output
filters.

Responses surface:

- `confidence_breakdown`
- `relations_filter`
- `hubs_skipped`
- `response_bytes`
- byte-cap metadata when `max_bytes` is provided by MCP callers

## MCP Server

Public launch path:

```bash
csegraph serve
```

Private module path:

```bash
python -m csegraph._core.server
```

The server exposes exactly six MCP tools:

- `csegraph_index`
- `csegraph_refresh`
- `csegraph_minimal`
- `csegraph_context`
- `csegraph_graph`
- `csegraph_path`

Public operations such as `analyze`, `export`, `registry`, and `daemon` are
CLI-only. Maintainer analytics remain behind `tools/csegraph_dev.py`.

The server wraps tool results with:

1. session-state recording
2. duplicate next-action filtering
3. optional byte-cap pruning
4. response-size annotations

## VS Code Extension

`csegraph-vscode/` is a sibling project. It shells out to the public `csegraph`
CLI for index, refresh, status, context, and inspect. It must not vendor or
reimplement graph logic.

## Release Layout

Publish one Python distribution:

```text
csegraph/
pyproject.toml
csegraph-vscode/
```

Forbidden tracked artifacts include `.csegraph/`, `.scratch/`, `dist/`,
`build/`, `*.egg-info`, `*.vsix`, and `csegraph-vscode/out/`.
