# Architecture

CseGraph is a local-first context system:

```text
repository
  -> discover Python, JavaScript, and TypeScript
  -> parse with tree-sitter
  -> build canonical files, symbols, relationships, and lexical data
  -> store schema v11 in SQLite
  -> retrieve compact context, graph neighborhoods, or paths
```

The runtime does not execute indexed code. Normal operation stays on the local
machine.

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
5. Build lexical data and summaries in the same write flow.
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

### Retrieval

`csegraph_context` combines lexical candidates, symbol resolution, import
bindings, summaries, one-hop graph evidence, and unresolved occurrence
evidence. Projections and result sets are bounded. The normal path returns
whole-symbol slices without loading the complete graph.

`csegraph_minimal` uses aggregate SQL and direct top-entity selection for
explicit health or orientation requests. `csegraph_graph` and
`csegraph_path` perform focused structural queries.

## SQLite schema v11

The schema identifier is `csegraph-sqlite-v11`; `PRAGMA user_version` is `11`.
The persisted tables are:

| Table | Purpose |
|---|---|
| `metadata` | Schema, repository, revision, and freshness metadata. |
| `files` | Canonical source-file records. |
| `symbols` | Canonical symbols linked to files and optional parent symbols. |
| `edges` | Unique directed relationships. |
| `imports` | Import statements and resolved files. |
| `import_bindings` | Local/imported names and resolution results. |
| `edge_occurrences` | Source locations supporting relationships or unresolved references. |
| `summaries` | Source-hash-keyed summaries for files and symbols. |
| `lexical_index` | FTS5 retrieval data. |
| `refresh_leases` | Cross-process refresh ownership and expiry. |

`entities` is a zero-storage SQL view that joins the canonical `files` and
`symbols` shapes for graph and path queries. Symbol path and language are
derived from the owning file.

Composite primary keys and `WITHOUT ROWID` are used where the access pattern
benefits. Indexes are limited to retrieval, traversal, refresh, and resolution
queries. Foreign keys are enabled and validated.

There is no schema migration. Any missing or non-v11 database produces
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
