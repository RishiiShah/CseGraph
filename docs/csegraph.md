# csegraph Command Reference

Runnable examples use the repository virtualenv. Installed users can replace
`env/bin/csegraph` with `csegraph`.

## Setup

```bash
env/bin/python -m pip install -e .
env/bin/csegraph install --platform auto
env/bin/csegraph install --platform codex --dry-run --json
env/bin/csegraph install --platform vscode
```

`install` configures MCP clients to launch `csegraph serve`. With
`--instructions`, it writes agent instruction files. With `--hooks`, it installs
supported refresh/status hooks.

## Index And Refresh

```bash
env/bin/csegraph index .
env/bin/csegraph index . --profile medium --postprocess full --json
env/bin/csegraph refresh .
env/bin/csegraph refresh . --postprocess minimal --json
env/bin/csegraph postprocess . --level full --json
env/bin/csegraph status . --verbose
```

Postprocess levels:

- `none`: parse/write only.
- `minimal`: rebuild FTS.
- `full`: rebuild FTS, resolver edges, and communities.

Default DB: `<repo>/.csegraph/index.db`.

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

- `csegraph_index`
- `csegraph_refresh`
- `csegraph_minimal`
- `csegraph_context`
- `csegraph_graph`
- `csegraph_path`

Agents should use minimal/context first and inspect/path only when the returned
next action calls for it.

## Public Operations

These are public CLI commands, not MCP tools:

```bash
env/bin/csegraph analyze . --json
env/bin/csegraph export . --format html --output graph.html
env/bin/csegraph export . --format tree
env/bin/csegraph export . --format json --output graph.json
env/bin/csegraph registry register /path/to/repo --alias app
env/bin/csegraph registry list
env/bin/csegraph daemon start --alias app
env/bin/csegraph daemon status
```

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

Supported config keys include profile, thresholds, top-k, graph radius, context
budget, and raw-code budget. Unknown keys raise `ValueError`.

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
