# CseGraph

CseGraph is a repository context engine for coding agents. It indexes source
code into a SQLite-backed graph, then returns the smallest task-specific slice
of files, symbols, dependencies, and explanations an agent needs.

Current release: `1.8.0`.

## Quick Start

Install CseGraph with `pipx`, then configure it from the repository you want to
index:

```bash
pipx install csegraph
cd /path/to/your/repository
csegraph install --platform codex
csegraph index .
```

Replace `codex` with `cursor`, `claude-code`, `gemini-cli`, `kiro`, `copilot`,
or `vscode`. Use `--platform auto` to configure every supported MCP client.

Retrieve context directly from the CLI:

```bash
csegraph context "explain how authentication refresh works" \
  --detail-level auto \
  --format markdown
```

CseGraph requires Python 3.10 or newer. See the
[platform-specific installation guide](https://github.com/RishiiShah/CseGraph#install)
for macOS, Linux, and Windows.

## Documentation

- [Command and SDK reference](csegraph.md) covers CLI commands, MCP tools, and
  the public Python facade.
- [Architecture](architecture.md) explains the parser, index, graph, retrieval,
  and MCP server boundaries.
- [Agent context benchmarks](benchmarks.md) records native MCP cross-repo
  benchmark results.
- [Token reduction benchmark](token-reduction-benchmark.md) records the current
  context-quality and token-reduction results.

## Core Loop

```text
index -> refresh -> context -> optional inspect/path/analyze
```

The repository index is stored locally at `.csegraph/index.db`. For contributor
setup and source development, see
[CONTRIBUTING.md](https://github.com/RishiiShah/CseGraph/blob/main/CONTRIBUTING.md).
