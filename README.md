# CseGraph

CseGraph is a local-first context system for coding agents. It indexes a
repository into SQLite, retrieves the smallest useful code slices for a task,
and exposes focused graph expansion only when structural evidence is needed.

CseGraph 2.0 is a hard compatibility cutoff:

- the index schema is `csegraph-sqlite-v11`;
- an index created by another schema must be rebuilt with `csegraph index`;
- there is no schema migration path;
- indexed source languages are Python, JavaScript, and TypeScript;
- the public surface is nine CLI commands and six strict MCP tools.

## Install

CseGraph requires Python 3.10 or newer.

```bash
python -m pip install csegraph
```

Create a fresh index:

```bash
csegraph index /path/to/repository
```

Retrieve task-specific context:

```bash
csegraph context "Fix stale cache invalidation" \
  --repo /path/to/repository \
  --target Cache.invalidate \
  --token-budget 800 \
  --format markdown
```

Register the MCP server for a supported coding client:

```bash
csegraph install /path/to/repository --platform codex
```

## CLI

The CLI has exactly nine commands:

| Command | Purpose |
|---|---|
| `csegraph index` | Build and atomically install a fresh v11 index. |
| `csegraph refresh` | Apply changed and deleted files to an existing v11 index. |
| `csegraph context` | Retrieve compact, budgeted task context. |
| `csegraph graph` | Inspect a focused graph neighborhood. |
| `csegraph path` | Find a focused dependency path. |
| `csegraph status` | Report index health and freshness. |
| `csegraph doctor` | Diagnose MCP client setup. |
| `csegraph install` | Register CseGraph with an MCP client. |
| `csegraph serve` | Start the MCP stdio server. |

See [the CLI and MCP reference](docs/csegraph.md) for all accepted arguments.

## Agent workflow

Call `csegraph_context` directly for ordinary coding tasks. Use
`csegraph_minimal` only for explicit index-health or repository-orientation
requests. Escalate to `csegraph_graph` or `csegraph_path` only when the compact
response recommends that focused structural operation.

The MCP server exposes exactly six tools:

| Tool | Purpose |
|---|---|
| `csegraph_index` | Build a fresh repository index. |
| `csegraph_refresh` | Refresh changed and deleted files. |
| `csegraph_minimal` | Return a small health or orientation summary. |
| `csegraph_context` | Return compact task-specific code slices. |
| `csegraph_graph` | Return a focused neighborhood. |
| `csegraph_path` | Return a focused dependency path. |

Every MCP input schema rejects unknown properties.

## Compact context v5

`csegraph_context` accepts `task`, `repo`, `target`, `task_kind`,
`token_budget`, `source_mode`, and `diagnostic`. The first two are required.
`diagnostic` is a boolean and defaults to `false`.

The only context response contract is `csegraph-context-v5`:

```json
{
  "schema_version": "csegraph-context-v5",
  "status": "ready",
  "slices": [
    {
      "path": "package/cache.py",
      "lines": [42, 61],
      "symbol": "Cache.invalidate",
      "role": "target",
      "code": "..."
    }
  ]
}
```

Depending on status and request, the response may also contain `candidates`,
`missing`, `next`, `warnings`, or `diagnostics`. Diagnostics are included only
when requested and remain inside the same whole-response token budget.

Every continuation has one shape:

```json
{
  "tool": "csegraph_graph",
  "arguments": {"repo": "/path/to/repository", "node": "Cache.invalidate"},
  "reason": "Inspect direct dependents."
}
```

`tool` is required; `arguments` and `reason` are optional.

## Index lifecycle

The repository index is stored at `.csegraph/index.db`. `csegraph index` builds
and validates a new database beside the active database, then replaces the
active database atomically. A failed build leaves the active database intact.
Successful replacement leaves no backup or migration artifact.

Commands that encounter a missing or non-v11 index report `index_required` and
direct the caller to `csegraph_index` or `csegraph index`.

## Benchmarks

Benchmark runners are maintainer tools under `tools/`; benchmarking is not a
product CLI or MCP operation. The tracked release gates and commands are
documented in [Agent Context Benchmarks](docs/benchmarks.md).

## Development

```bash
python -m pip install -e ".[test,dev]"
pytest -q
ruff check .
mypy
```

## Privacy

Indexing, retrieval, refresh, and MCP operation run locally. CseGraph does not
execute indexed code and normal operation requires no network request.

## License

MIT
