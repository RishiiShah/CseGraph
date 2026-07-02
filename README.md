<h1 align="center">CseGraph</h1>

<p align="center">
  <strong>Give coding agents the code they need—not the whole repository.</strong>
</p>

<p align="center">
  <a href="https://github.com/RishiiShah/CseGraph/actions/workflows/ci.yml"><img src="https://github.com/RishiiShah/CseGraph/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/csegraph/"><img src="https://img.shields.io/pypi/v/csegraph?style=flat-square&color=blue" alt="PyPI"></a>
  <a href="https://pypi.org/project/csegraph/"><img src="https://img.shields.io/pypi/pyversions/csegraph?style=flat-square" alt="Python"></a>
  <a href="LICENSE"><img src="https://img.shields.io/pypi/l/csegraph?style=flat-square" alt="License"></a>
  <a href="https://marketplace.visualstudio.com/items?itemName=rishiishah.csegraph-vscode"><img src="https://img.shields.io/visual-studio-marketplace/i/rishiishah.csegraph-vscode?style=flat-square&label=VS%20Code" alt="VS Code installs"></a>
  <a href="https://modelcontextprotocol.io/"><img src="https://img.shields.io/badge/MCP-compatible-green?style=flat-square" alt="MCP compatible"></a>
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> ·
  <a href="#install">Install</a> ·
  <a href="#set-up-your-coding-agent">Agent Setup</a> ·
  <a href="#how-it-works">How It Works</a> ·
  <a href="#benchmarks">Benchmarks</a> ·
  <a href="docs/csegraph.md">CLI & MCP Reference</a>
</p>

<br>

Coding agents often spend time and tokens searching broadly, reading entire
files, and repeating lookups. CseGraph builds a local dependency graph of your
repository and returns a small, task-specific context bundle containing the
relevant code, dependencies, imports, nearby tests, and selection reasons.

## Quick Start

```bash
pip install csegraph                         # or: uv tool install csegraph
cd /path/to/your/repository
csegraph install --platform auto             # configure supported MCP clients
csegraph index                               # parse your codebase
```

One command sets up your coding agent integrations. `install` detects supported
MCP clients, writes the correct platform config, adds local agent instructions
and verifies the generated MCP launcher. Freshness uses one persistent,
debounced watcher plus one lightweight safety refresh at the end of an agent
turn—never a refresh or status command after every tool call. Restart your editor
or coding tool after installing.

Then ask your agent to use CseGraph, or query it directly:

```bash
csegraph context "explain how authentication refresh works" \
  --detail-level auto \
  --format markdown
```

The local index is stored at `.csegraph/index.db` and should not be committed.

## Install

CseGraph requires Python 3.10 or newer. Install it from PyPI:

```bash
pip install csegraph
```

Prefer an isolated CLI tool install? Use one of these instead:

```bash
uv tool install csegraph
pipx install csegraph
```

On Windows, install [Python 3.10 or newer](https://www.python.org/downloads/windows/),
then use PowerShell:

```powershell
py -m pip install csegraph
```

If your shell cannot find `csegraph` after a pip install, add Python's scripts
directory to your `PATH`, use `python3 -m pip install --user csegraph` on
macOS/Linux, or use `uv tool install csegraph`.

To run without a persistent install:

```bash
uvx csegraph --help
```

Verify the install:

```bash
csegraph --help
```

The base package includes Python, JavaScript, and TypeScript grammars. Add more
grammars or pin the current release like this:

```bash
pip install "csegraph[go,rust]"
pip install "csegraph[all]"
pip install csegraph==1.8.1
```

## Set Up Your Coding Agent

Run the matching command from the root of the repository you want CseGraph to
index:

| Platform | Command |
|---|---|
| All supported MCP clients | `csegraph install --platform auto` |
| Codex | `csegraph install --platform codex` |
| Cursor | `csegraph install --platform cursor` |
| Claude Code | `csegraph install --platform claude-code` |
| Gemini CLI | `csegraph install --platform gemini-cli` |
| Kiro | `csegraph install --platform kiro` |
| Antigravity CLI | `csegraph install --platform antigravity-cli` |
| Antigravity IDE global config | `csegraph install --platform antigravity-ide` |
| GitHub Copilot | `csegraph install --platform copilot` |
| VS Code project files | `csegraph install --platform vscode` |

The installer configures the MCP server, writes platform-scoped agent
instructions, installs a lightweight end-of-turn refresh hook, verifies the
generated `csegraph serve --repo <repo> --platform <client>` MCP launcher, and
adds generated local files to `.gitignore`. Use `csegraph daemon start` for
continuous freshness and `--no-hooks` to disable the safety refresh. Hook
refreshes are bounded to git-detected changed paths, so they avoid full-repo
scans during agent turns. Each client gets its own platform tag, so a Cursor MCP
call is not treated as Codex setup. Antigravity IDE writes user-global config
only when explicitly selected. Preview the changes without writing files:

On macOS, Linux, and Windows, generated MCP configs use a native absolute
`csegraph` executable path. Windows virtualenv and pipx installs are resolved
to `Scripts\csegraph.exe`, `.cmd`, or `.bat` automatically, so you should not
need to edit `.mcp.json`, `.cursor/mcp.json`, or other generated MCP files by
hand after installation. Python user installs are handled too: if `pip install
--user csegraph` places the CLI under `%APPDATA%\Python\PythonXY\Scripts` on
Windows or `~/.local/bin` on Linux, the installer can still write that absolute
launcher path even when the folder is not on your terminal `PATH`.

```bash
csegraph install --platform codex --dry-run
```

Use `--hooks` to install hooks for every supported agent, or
`--no-hooks`, `--no-instructions`, `--no-gitignore`, or `--no-verify` to
customize setup. Diagnose a platform with:

```bash
csegraph doctor --platform auto --json
csegraph doctor --platform codex --require-observed-call --json
```

`doctor --platform auto` checks every project-scoped client config and reports
which are missing, protocol-verified, or still waiting for real host use. After
installing, open that client's MCP/tools settings and enable or approve the
`csegraph` server so the six CseGraph tools are visible. A `.csegraph` index or
another client's config is not enough; Codex, Cursor, Claude Code, and the other
hosts each need their own enabled MCP entry. Agents should not query
`.csegraph/index.db` directly or use CLI context commands as a substitute for
that platform's MCP server.

## How It Works

```mermaid
flowchart TD
    A["Your repository"] --> B["Tree-sitter indexer"]
    B --> C["Local SQLite<br/>dependency graph"]
    C --> D["Adaptive lexical retrieval"]
    D --> E["Graph reranking when needed"]
    E --> F["Exact-budget code slices"]
    F --> G["Coding agent"]
```

1. `csegraph index` parses supported source files into symbols, imports, calls,
   inheritance relationships, and test links.
2. The `csegraph_context` MCP tool performs indexed lexical retrieval first,
   uses graph relationships only when ambiguity or impact requires them, and
   packages the result under an exact whole-response token budget.
3. The optional `csegraph_minimal` tool reports index health and repository
   entry points without being required before ordinary context retrieval.
4. `csegraph refresh` updates changed and deleted files without rebuilding
   everything.

For structural questions, CseGraph can inspect graph neighborhoods or find the
shortest dependency path between two symbols.

## Common Commands

```bash
# Build the initial index
csegraph index

# Refresh changed files
csegraph refresh

# Watch the repository and refresh automatically
csegraph watch

# Check index health
csegraph status --verbose

# Retrieve context for a task
csegraph context "fix auth token refresh" --target refresh_token

# Inspect callers, callees, imports, and test relationships
csegraph inspect ContextService.build_context --depth 1

# Find a dependency path
csegraph path IndexService.index ContextService.build_context

# Export an interactive graph
csegraph export --format html --output graph.html
```

See the [CLI and MCP reference](docs/csegraph.md) for every command and flag.

## Features

| Feature | What it provides |
|---|---|
| Minimal context retrieval | Task-specific code, relationships, imports, tests, and selection reasons |
| Incremental refresh | Re-indexes changed and deleted files instead of rebuilding the whole graph |
| MCP integration | Six focused tools for indexing, refreshing, routing, context, neighborhoods, and paths |
| Local-first storage | Repository indexes stay in `.csegraph/index.db` |
| Monorepo scoping | Repeatable `--include-root` options limit indexing to selected subtrees |
| Local context includes | `.csegraphinclude` safely opts selected ignored code or internal docs into the local index |
| Multiple profiles | `auto`, `small`, `medium`, and `large` retrieval profiles |
| Graph export | HTML, tree, JSON, GraphML, and Obsidian output |
| Editor support | MCP setup for major coding agents plus a VS Code extension |
| Public Python API | Sync and async services for custom integrations |

## Language Support

The base package includes:

- Python
- JavaScript
- TypeScript and TSX

Optional grammars include Go, Rust, Java, C, C++, Ruby, C#, Kotlin, Groovy,
Scala, PHP, Swift, Lua, Zig, PowerShell, Elixir, Objective-C, Julia, Verilog,
and Fortran.

Install selected extras such as `csegraph[go,rust]`, or use `csegraph[all]` for
every available grammar.

## MCP Tools

| Tool | Purpose |
|---|---|
| `csegraph_index` | Build a repository graph |
| `csegraph_refresh` | Refresh changed and deleted files |
| `csegraph_minimal` | Optional index-health and repository-orientation card |
| `csegraph_context` | Retrieve exact-budget adaptive code slices in one call |
| `csegraph_graph` | Inspect a graph neighborhood |
| `csegraph_path` | Find the shortest path between two nodes |

Agents should call `csegraph_context` directly for task-specific code. Use
`csegraph_minimal` only for health or orientation, and use graph/path only when
the compact response recommends structural escalation.

## Benchmarks

CseGraph 2.0 is evaluated against a strong, reproducible `rg` plus selective
read baseline rather than a full-repository-read strawman. The baseline ranks
JSON ripgrep matches, reads bounded 80-line windows, follows imports once, and
uses the same exact token budget as adaptive retrieval.

```bash
env/bin/python tools/run_adaptive_retrieval_benchmark.py \
  --corpus benchmarks/adaptive/pr_tasks.json \
  --fail-on-gates
```

The report measures target resolution, required-slice recall and precision,
whole-response tokens, latency, tool calls, cache state, and freshness. See
[Agent Context Benchmarks](docs/benchmarks.md) for methodology and release gates.

## VS Code

Install the
[CseGraph extension from the Marketplace](https://marketplace.visualstudio.com/items?itemName=rishiishah.csegraph-vscode),
then run:

```bash
csegraph install --platform vscode
```

Open the repository in VS Code and run **CseGraph: Build Index** from the
command palette. See the [extension guide](csegraph-vscode/README.md) for
commands, settings, keybindings, and troubleshooting.

## Privacy

Normal indexing, retrieval, refresh, MCP, and VS Code operations run locally.
Repository indexes are written under `.csegraph/`; registry and daemon metadata
are stored under `~/.csegraph/`.

No network request is required for normal operation. Optional embeddings can
call an OpenAI-compatible endpoint only when explicitly configured and allowed
with `CSEGRAPH_ALLOW_CLOUD_EMBEDDINGS`.

## Documentation

- [CLI, MCP, and SDK reference](docs/csegraph.md)
- [Architecture](docs/architecture.md)
- [Benchmarks](docs/benchmarks.md)
- [Contributing](CONTRIBUTING.md)
- [Support](SUPPORT.md)
- [Security](SECURITY.md)
- [Changelog](CHANGELOG.md)

## License

CseGraph is released under the [MIT License](LICENSE).
