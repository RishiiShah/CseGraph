# CseGraph CLI and MCP Reference

CseGraph 2.0 exposes a deliberately small public surface. The CLI contains
exactly nine commands and the MCP server contains exactly six tools.

All repository arguments resolve to an absolute repository root. The index
location is always `<repo>/.csegraph/index.db`.

## CLI

`csegraph -v` enables informational logging; repeat `-v` for debug logging.

### `index`

Build, validate, and atomically install a fresh schema-v11 index.

```text
csegraph index [REPO] [--repo REPO]
               [--exclude PATTERN]...
               [--include-root PATH]...
               [--json]
```

`--exclude` adds an ignore pattern. `--include-root` limits discovery to a
repository-relative subtree. Both may be repeated.

### `refresh`

Refresh changed and deleted files in an existing schema-v11 index.

```text
csegraph refresh [REPO] [--repo REPO]
                 [--exclude PATTERN]...
                 [--include-root PATH]...
                 [--json]
```

### `context`

Retrieve compact adaptive context for a coding task.

```text
csegraph context TASK [--repo REPO]
                 [--target TARGET]
                 [--task-kind {auto,edit,understand,review,test-impact}]
                 [--token-budget TOKENS]
                 [--source-mode {auto,always,never}]
                 [--diagnostic]
                 [--format {json,markdown}]
```

Defaults are `task-kind=auto`, `token-budget=800`, `source-mode=auto`, and
`format=json`. The token budget covers the complete serialized response.

### `graph`

Inspect a focused graph neighborhood.

```text
csegraph graph NODE [--repo REPO]
               [--depth DEPTH]
               [--relations RELATION,...]
               [--json]
```

Depth defaults to one. Use this command when context recommends focused
structural expansion.

### `path`

Find a focused dependency path.

```text
csegraph path SOURCE TARGET [--repo REPO]
              [--relations RELATION,...]
              [--json]
```

### `status`

Report index health and freshness.

```text
csegraph status [REPO] [--repo REPO] [--json]
```

### `doctor`

Diagnose MCP client setup.

```text
csegraph doctor [REPO] [--repo REPO]
                [--platform PLATFORM]
                [--command COMMAND]
                [--no-verify]
                [--json]
```

Use `csegraph doctor --help` to list the accepted client platforms.

### `install`

Register CseGraph with an MCP client.

```text
csegraph install [REPO] [--repo REPO]
                 [--platform PLATFORM]
                 [--command COMMAND]
                 [--dry-run]
                 [--no-verify]
                 [--json]
```

The accepted platforms are the same as for `doctor`.

### `serve`

Start the MCP stdio server.

```text
csegraph serve [--repo REPO]
               [--tools core|TOOL,...]
               [--platform PLATFORM]
```

`--tools` accepts `core` or a comma-separated subset of the six MCP tool names.

## MCP tools

Every tool uses a strict object input schema with
`additionalProperties: false`.

### `csegraph_index`

Build a fresh index.

```json
{"repo": "/absolute/repository"}
```

`repo` is required.

### `csegraph_refresh`

Refresh changed and deleted files.

```json
{"repo": "/absolute/repository"}
```

`repo` is required.

### `csegraph_minimal`

Return a small index-health or repository-orientation summary. Ordinary coding
tasks should call `csegraph_context` directly.

```json
{
  "repo": "/absolute/repository",
  "task": "Optional orientation task"
}
```

`repo` is required. `task` is optional. The response contains `summary`, no
more than three `entities`, and at most one `next` continuation.

### `csegraph_context`

Primary task-specific retrieval.

```json
{
  "task": "Fix stale cache invalidation",
  "repo": "/absolute/repository",
  "target": "Cache.invalidate",
  "task_kind": "edit",
  "token_budget": 800,
  "source_mode": "auto",
  "diagnostic": false
}
```

`task` and `repo` are required. The remaining fields are optional:

| Field | Accepted value | Default |
|---|---|---|
| `target` | symbol, node ID, or repository-relative path | omitted |
| `task_kind` | `auto`, `edit`, `understand`, `review`, `test-impact` | `auto` |
| `token_budget` | integer from 256 through 16384 | `800` |
| `source_mode` | `auto`, `always`, `never` | `auto` |
| `diagnostic` | boolean | `false` |

### `csegraph_graph`

Inspect a focused neighborhood.

```json
{
  "node": "Cache.invalidate",
  "repo": "/absolute/repository",
  "depth": 1,
  "relations": ["calls"],
  "confidence_tiers": ["EXTRACTED"]
}
```

`node` and `repo` are required. `depth` is an integer from one through three.
`relations` and `confidence_tiers` are optional string arrays.

### `csegraph_path`

Find a focused dependency path.

```json
{
  "source": "Cache.invalidate",
  "target": "Cache.get",
  "repo": "/absolute/repository",
  "relations": ["calls"],
  "confidence_tiers": ["EXTRACTED"]
}
```

`source`, `target`, and `repo` are required. `relations` and
`confidence_tiers` are optional string arrays.

## Compact response contracts

### Context v5

`csegraph-context-v5` is the only context response contract:

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

The required top-level fields are `schema_version`, `status`, and `slices`.
`status` is `ready`, `ambiguous`, `insufficient`, `index_required`, or
`refresh_required`.

Conditional top-level fields are:

- `candidates` when target resolution needs a choice;
- `missing` when more evidence is required;
- `next` for one focused recovery or structural operation;
- `warnings` for actionable non-fatal conditions;
- `diagnostics` only when `diagnostic` is `true`.

Each slice contains `path`, `lines`, `symbol`, `role`, and `code`. Diagnostic
data is subject to the same whole-response token budget and may be omitted to
keep the response within that budget.

CLI JSON and Markdown carry the same semantic fields.

### Continuations

Every continuation uses:

```json
{
  "tool": "csegraph_context",
  "arguments": {
    "repo": "/absolute/repository",
    "task": "Fix stale cache invalidation"
  },
  "reason": "Retry after refreshing the index."
}
```

`tool` is required. `arguments` and `reason` are optional. No alternate
argument field is accepted.

### Graph and path

Graph results use `csegraph-graph-v2`. Path results use
`csegraph-path-v2`. These compact serializers omit empty fields and internal
database or repository locations.

## Schema compatibility

The required database schema is `csegraph-sqlite-v11`
(`PRAGMA user_version = 11`). There is no migration from any other schema.
Rebuild with:

```bash
csegraph index /absolute/repository
```

A missing or incompatible index produces `index_required` and a continuation
for `csegraph_index`. The build occurs beside the active database, is validated
before replacement, and is installed atomically. Failure preserves the active
database.

## Public Python facade

The package root lazily exposes:

- `IndexService`, `RefreshService`, `StatusService`, `ContextService`,
  `MinimalService`, and `GraphQueryService`;
- `ContextRequest`, `ContextResponse`, `ContextSlice`, `ContextStatus`, and
  `ContextTarget`;
- `IndexResult`, `RefreshResult`, `StatusResult`, `MinimalResult`,
  `GraphResult`, and `PathResult`;
- `IndexRequiredError`, `to_dict`, and `__version__`.

Only Python, JavaScript, and TypeScript source files are indexed.

## Agent instructions

Use one `csegraph_context` call for an ordinary task. Use
`csegraph_minimal` only when the request is explicitly about health or
orientation. Call `csegraph_graph` or `csegraph_path` only when `next`
recommends that focused operation.
