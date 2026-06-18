# CseGraph

CseGraph is a repository context engine for coding agents. It indexes source
code into a SQLite-backed graph, then returns the smallest task-specific slice
of files, symbols, dependencies, and explanations an agent needs.

## Start Here

- [Command and SDK reference](csegraph.md) covers CLI commands, MCP tools, and
  the public Python facade.
- [Architecture](architecture.md) explains the parser, index, graph, retrieval,
  and MCP server boundaries.
- [Token reduction benchmark](token-reduction-benchmark.md) records the current
  context-quality and token-reduction results.

## Core Loop

```text
index -> refresh -> context -> optional inspect/path/analyze
```

For local development, install from the repository root:

```bash
env/bin/python -m pip install -e ".[test,dev,all,docs]"
```
