# Changelog

All notable changes to CseGraph are recorded here.

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
