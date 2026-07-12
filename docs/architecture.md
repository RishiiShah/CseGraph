# Architecture

CseGraph is a local-first context system:

```text
repository
  -> discover Python, JavaScript, and TypeScript
  -> parse with tree-sitter
  -> build canonical files, symbols, relationships, and lexical data
  -> store schema v12 in SQLite
  -> retrieve compact context, graph neighborhoods, or paths
```

The runtime does not execute indexed code. Normal operation stays on the local
machine.

Generated workspace state is not part of the product source. Local indexes and
parse caches live under `.csegraph/`; build outputs, environments, dependency
directories, benchmark reports, and sandbox clones are disposable and ignored
by git. Benchmark definitions under `tools/benchmarks/` are source; generated
benchmark reports are evidence outputs only.

## Public boundaries

The CLI boundary contains exactly nine commands: `index`, `refresh`, `context`,
`graph`, `path`, `status`, `doctor`, `install`, and `serve`.

The MCP boundary contains exactly six strict tools: `csegraph_index`,
`csegraph_refresh`, `csegraph_minimal`, `csegraph_context`, `csegraph_graph`,
and `csegraph_path`. Each input schema rejects unknown properties.

The package root is a lazy Python facade over the core indexing, retrieval,
graph-query, compact result, serialization, and typed recovery APIs.

## Core flow

### Fresh indexing

1. Resolve the repository and selected roots.
2. Discover supported source files.
3. Parse source and extract symbols, imports, bindings, relationships, and
   occurrence evidence.
4. Write a new database beside the active database.
5. Build persisted module/symbol lookups, lexical data, and summaries in the
   same write flow.
6. Validate foreign keys and database integrity.
7. Optimize and close the new database.
8. Atomically replace the active database.

If any step fails before replacement, the active database remains untouched
and temporary build artifacts are removed. A successful rebuild leaves no
backup or migration artifact.

### Incremental refresh

Refresh computes changed and deleted files against the current index, captures
dependents from current relationships before replacement, and updates only the
affected rows. File and symbol foreign keys cascade cleanup. A repository lease
coordinates concurrent refresh attempts.

The implementation keeps these responsibilities separate: `index/services.py`
coordinates the public indexing operations, `index/ingestion.py` owns parsing
and cache reads, `index/writer.py` owns parsed-file persistence and graph writes,
`index/lookups.py` owns lazy lookup state, `index/refresh_plan.py` owns impact
queries, and `index/resolution.py` owns import/call/edge resolution. Freshness
coordination is similarly split into `retrieval/freshness/coordinator.py`,
`lease.py`, and `scan.py`.

### Retrieval

`csegraph_context` combines lexical candidates, symbol resolution, import
bindings, summaries, one-hop graph evidence, and unresolved occurrence
evidence. Projections and result sets are bounded. The normal path returns
whole-symbol slices without loading the complete graph.

`csegraph_minimal` uses aggregate SQL and direct top-entity selection for
explicit health or orientation requests. `csegraph_graph` and
`csegraph_path` perform focused structural queries.

## SQLite schema v12

The schema identifier is `csegraph-sqlite-v12`; `PRAGMA user_version` is `12`.
The persisted tables are:

| Table | Purpose |
|---|---|
| `metadata` | Schema, repository, revision, and freshness metadata. |
| `files` | Canonical source-file records. |
| `symbols` | Canonical symbols linked to files and optional parent symbols. |
| `module_lookup` | Module names mapped to canonical file identifiers. |
| `symbol_lookup` | Full and short symbol aliases mapped to canonical symbols. |
| `edges` | Unique directed relationships. |
| `imports` | Import statements and resolved files. |
| `import_bindings` | Local/imported names and resolution results. |
| `edge_occurrences` | Source locations supporting relationships or unresolved references. |
| `summaries` | Source-hash-keyed summaries for files and symbols. |
| `lexical_documents` | Canonical lexical rows with indexed node identifiers. |
| `lexical_index` | External-content FTS5 retrieval index maintained from lexical documents. |
| `refresh_leases` | Cross-process refresh ownership and expiry. |

`entities` is a zero-storage SQL view that joins the canonical `files` and
`symbols` shapes for graph and path queries. Symbol path and language are
derived from the owning file.

Composite primary keys and `WITHOUT ROWID` are used where the access pattern
benefits. Indexes are limited to retrieval, traversal, refresh, and resolution
queries. Foreign keys are enabled and validated.

Fresh builds bulk-populate lexical documents and rebuild FTS once before atomic
publication. Secondary indexes are constructed after graph writes on the
disposable build database. Incremental writes use SQLite triggers, so canonical
rows and FTS updates commit or roll back together. Refresh resolution loads only
the module and symbol aliases referenced by the changed batch.

There is no schema migration. Any missing or non-v12 database produces
`index_required` with a `csegraph_index` continuation.

## Compact contracts

Context uses `csegraph-context-v5`. Its required fields are `schema_version`,
`status`, and `slices`; `candidates`, `missing`, `next`, `warnings`, and
`diagnostics` are conditional. The request fields are `task`, `repo`, `target`,
`task_kind`, `token_budget`, `source_mode`, and the boolean `diagnostic`.

Graph and path use `csegraph-graph-v2` and `csegraph-path-v2`. Their serializers
omit internal locations, empty fields, and default values.

Every continuation is `{tool, arguments?, reason?}`.

## Performance invariants

- Importing `csegraph` remains lazy.
- Ordinary context retrieval avoids a complete-graph snapshot.
- SQL reads use narrow projections and bounded result sets.
- Diagnostic data remains within the requested whole-response token budget.
- Fresh indexing validates before atomic replacement.
- Runtime dependencies are limited to MCP, tree-sitter, and the Python,
  JavaScript, and TypeScript tree-sitter grammars.
