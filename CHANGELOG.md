# Changelog

All notable changes to CseGraph are recorded here.

## Unreleased

### Added

- Added bidirectional two-hop cascade expansion to impact and structural
  retrieval, including cascade distance in diagnostics.
- Added pushed `context` slices and `impacted_files` to refresh results, so a
  database update and its relevant retrieval happen in one call.
- Added user-facing ATSE-1000 terse prose and internal ATE-1000 compaction.

### Changed

- Reduced exact-target ambiguity thresholds and increased default role
  selection so retrieval returns cascading callers instead of a single match.

### Fixed

- Preserve pushed context for deletions, mixed changes, and concurrent refresh
  reuse, including a revision published just before lease acquisition.
- Preserve dependent context when symbols are removed or renamed inside a
  surviving file or dependents connect only through file imports, including reused
  refresh results.
- Report dependent refresh overflow only when another dependent is omitted.
- Continue dependent refresh through files containing only imports; expand
  inferred dispatch and registration references at deeper retrieval hops.
- Preserve direct relationship roles, honor explicit one-hop requests, and keep
  requested dependencies alongside relevant tests within slice limits.
- Skip oversized source slices without hiding smaller context; report missing
  source and reject invalid refresh token budgets before writes.
- Preserve quoted whitespace, code blocks, signatures, and symbol names during
  ATSE/ATE compression.
- Preserve stopword symbol names in path summaries, keep decorator roles
  consistent across retrieval paths, and bound cumulative expansion relationships.

## 2.0.1

### Fixed

- Made exact `tiktoken` counting a runtime dependency so normal installations
  enforce token budgets exactly.
- Made bounded ripgrep benchmark candidate collection stable by sorting paths
  before truncation.

## 2.0.0

### Added

- Added the compact `csegraph-context-v5` response contract with
  status-specific `slices`, `candidates`, `missing`, `next`, `warnings`, and
  opt-in `diagnostics`.
- Added the boolean `diagnostic` context argument. Diagnostic data shares the
  same whole-response token budget as the rest of the response.
- Added versioned compact graph and path responses:
  `csegraph-graph-v2` and `csegraph-path-v2`.
- Added typed `IndexRequiredError` recovery through the public Python facade.
- Added atomic fresh-index replacement with integrity validation and
  failure-safe preservation of the active index.

### Changed

- Locked the CLI to exactly `index`, `refresh`, `context`, `graph`, `path`,
  `status`, `doctor`, `install`, and `serve`.
- Locked MCP to exactly `csegraph_index`, `csegraph_refresh`,
  `csegraph_minimal`, `csegraph_context`, `csegraph_graph`, and
  `csegraph_path`.
- Made every MCP input schema reject unknown properties.
- Standardized continuations as `{tool, arguments?, reason?}`.
- Made `csegraph_context` the direct entry point for ordinary tasks and
  reserved `csegraph_minimal` for explicit health or orientation requests.
- Limited source indexing to Python, JavaScript, and TypeScript.
- Moved benchmark execution to repository-maintainer tools.

### Index compatibility

- Replaced the on-disk format with `csegraph-sqlite-v11`.
- Removed schema migration. Every non-v11 index must be regenerated with
  `csegraph index`.
- Made `files` and `symbols` canonical and exposed their union through the
  zero-storage `entities` SQL view.
- Limited persisted data to retrieval, graph traversal, resolution, summaries,
  freshness coordination, and index metadata.

### Release gates

- Required `pytest -q`, Ruff, and mypy to pass.
- Added contract coverage for every context status, whole-response budgeting,
  diagnostics, JSON/Markdown parity, continuation shape, graph/path output,
  CLI help, MCP schemas, and the public Python facade.
- Added indexing and retrieval coverage for Python, JavaScript, and TypeScript.
