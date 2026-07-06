# CseGraph 2.0 Roadmap

The 2.0 direction is a hard compatibility cutoff around one adaptive retrieval
path and focused structural escalation.

## 1. Freeze public contracts

- Lock the CLI to `index`, `refresh`, `context`, `graph`, `path`, `status`,
  `doctor`, `install`, and `serve`.
- Lock MCP to `csegraph_index`, `csegraph_refresh`, `csegraph_minimal`,
  `csegraph_context`, `csegraph_graph`, and `csegraph_path`.
- Reject unknown MCP arguments.
- Make `csegraph-context-v5` the only context response.
- Use a boolean `diagnostic` request field.
- Standardize continuations as `{tool, arguments?, reason?}`.

## 2. Rebuild the index

- Require `csegraph-sqlite-v11`.
- Keep canonical `files` and `symbols` records.
- Use the zero-storage `entities` view for graph and path queries.
- Retain only metadata, files, symbols, relationships, imports, bindings,
  occurrence evidence, summaries, lexical data, and refresh leases.
- Require a fresh `csegraph index` for every non-v11 database; provide no
  migration path.
- Build beside the active database, validate, close, and atomically replace it.

## 3. Keep retrieval compact

- Call context directly for ordinary work.
- Reserve minimal for explicit health or orientation requests.
- Use bounded lexical, resolution, summary, and one-hop relationship evidence.
- Escalate to graph or path only for focused structural work.
- Keep diagnostic data inside the whole-response token budget.
- Index only Python, JavaScript, and TypeScript.

## 4. Enforce release gates

- Pass `pytest -q`, Ruff, and mypy.
- Contract-test all context statuses, JSON/Markdown parity, budgeting,
  diagnostics, continuations, graph/path serializers, CLI help, strict MCP
  schemas, and the Python facade.
- Test fresh indexing, mandatory reindex recovery, atomic replacement,
  replacement-failure preservation, changed/deleted-file refresh, and database
  integrity.
- Hold the 20-task adaptive corpus to 100% target/status/recall, at least 95%
  precision, at most 35% median token ratio, and sub-100 ms p95 CseGraph
  overhead.
- Run a balanced 60-task nightly corpus split evenly between Python and
  JavaScript/TypeScript.
- Dogfood a fresh v11 index against the database, wheel, import, memory, and
  indexing limits documented in [benchmarks.md](benchmarks.md).
