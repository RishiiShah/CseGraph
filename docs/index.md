# CseGraph

CseGraph is a repository context engine for coding agents. It indexes source
code into a SQLite-backed graph, then returns the smallest task-specific slice
of files, symbols, and dependencies an agent needs.

Current release: `2.0.0`.

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
  --format markdown
```

CseGraph requires Python 3.10 or newer. See the
[README installation section](https://github.com/RishiiShah/CseGraph#install)
for package installation examples.

## Documentation

- [Command and SDK reference](csegraph.md) covers CLI commands, MCP tools, and
  the public Python facade.
- [Architecture](architecture.md) explains the parser, index, graph, retrieval,
  and MCP server boundaries.
- [Agent context benchmarks](benchmarks.md) describes the adaptive retrieval
  PR and nightly gates.

## Core Loop

```text
index -> refresh -> context -> optional graph/path
```

The repository index is stored locally at `.csegraph/index.db`. For contributor
setup and source development, see
[CONTRIBUTING.md](https://github.com/RishiiShah/CseGraph/blob/main/CONTRIBUTING.md).
