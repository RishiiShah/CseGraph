# CseGraph

CseGraph is a repository context engine for coding agents. It indexes source
code into a SQLite-backed graph, then returns the smallest task-specific slice
of files, symbols, dependencies, and explanations an agent needs.

Current release: `1.8.1`.

## Quick Start

Install CseGraph with `pip`, then configure it from the repository you want to
index:

```bash
pip install csegraph                         # or: uv tool install csegraph
cd /path/to/your/repository
csegraph install --platform auto
csegraph index
```

Use `uv tool install csegraph` for an isolated CLI install, or `pipx install
csegraph` if that is your preferred tool runner. Replace `auto` with `codex`,
`cursor`, `claude-code`, `gemini-cli`, `kiro`, `antigravity-cli`, `copilot`, or
`vscode` to configure one client. Use `--platform antigravity-ide` only when
you explicitly want user-global Antigravity IDE config.

Check project-scoped MCP setup with:

```bash
csegraph doctor --platform auto --json
```

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
  benchmark results, including context-size reductions from sandbox workloads.

## Core Loop

```text
index -> refresh -> context -> optional inspect/path/analyze
```

The repository index is stored locally at `.csegraph/index.db`. For contributor
setup and source development, see
[CONTRIBUTING.md](https://github.com/RishiiShah/CseGraph/blob/main/CONTRIBUTING.md).
