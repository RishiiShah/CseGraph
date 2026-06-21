# csegraph

[![CI](https://github.com/RishiiShah/CseGraph/actions/workflows/ci.yml/badge.svg)](https://github.com/RishiiShah/CseGraph/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/csegraph.svg)](https://pypi.org/project/csegraph/)
[![Python](https://img.shields.io/pypi/pyversions/csegraph.svg)](https://pypi.org/project/csegraph/)
[![License](https://img.shields.io/pypi/l/csegraph.svg)](LICENSE)
[![VS Code installs](https://img.shields.io/visual-studio-marketplace/i/rishiishah.csegraph-vscode?label=VS%20Code%20installs)](https://marketplace.visualstudio.com/items?itemName=rishiishah.csegraph-vscode)

CseGraph is a **context engine for coding agents**. Its only job is to hand an agent the accurate, minimal slice of code context needed to make a correct retrieval or edit, so the agent spends fewer tokens and skips tool calls it would otherwise make (broad grep, full-file read, repeated lookups).

It indexes source code into a SQLite-backed dependency graph, then returns compact, task-specific context bundles before an agent edits.

The product loop is:

```text
index -> refresh -> context -> optional inspect/path/analyze
```

Use csegraph when you want an agent to see the target code, direct dependencies, imports, nearby tests, and a short explanation of why each node was selected without repeatedly scanning the repository.

## Install

Current release: `1.8.0`.

```bash
pip install csegraph
```

To pin this release exactly:

```bash
pip install csegraph==1.8.0
```

The base package includes Python, JavaScript, and TypeScript grammars. Install
extra grammars only when you need them:

```bash
pip install "csegraph[go,rust]"
pip install "csegraph[all]"
```

Then run `csegraph --help` to confirm the CLI is on your PATH.

## Five Minute Quickstart

```bash
cd /path/to/your/repo
csegraph index .
csegraph context "explain how authentication refresh works" --detail-level standard --format markdown
```

The default index lives at `.csegraph/index.db`. It is local runtime state and
should not be committed.

For VS Code extension install and setup, see
[csegraph-vscode/README.md](csegraph-vscode/README.md).

## Benchmarks & Performance

The native MCP cross-repo benchmarking suite (`tools/cross_repo_benchmark.py`)
evaluates CseGraph against 10 major open-source repositories, generating 100
unique architectural queries per repository through the same stdio JSON-RPC path
used by coding agents. Current `auto`, `small`, `medium`, and `large` profile
results are recorded in the [Agent Context Benchmarks](docs/benchmarks.md).

## Package Layout

| Package | Location | Purpose |
|---|---|---|
| `csegraph` | repo root | One Python distribution containing the public CLI, MCP server, SDK facade, and private engine internals. |
| `csegraph-vscode` | `csegraph-vscode/` | VS Code extension source. See [extension README](csegraph-vscode/README.md). |

Public Python imports use `csegraph`. Internal implementation modules live under `csegraph._core` and `csegraph._cli`; they are not documented as public API.

## Install From Source

```bash
env/bin/pip install -e .
```

For local development and test runs, install the test extra:

```bash
env/bin/python -m pip install -e ".[test,all]"
```

For benchmark reports with OpenAI proxy token counts, include the benchmark
extra:

```bash
env/bin/python -m pip install -e ".[benchmark,test,all]"
```

`requirements.txt` contains the product-only editable install.

This repository is source-first. The public project is distributed as one Python
package and the VS Code extension source; generated binaries, local graph
databases, build outputs, and dashboard artifacts are not committed.

## Project Hygiene

- Security policy: [SECURITY.md](SECURITY.md)
- Code of conduct: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- Support guide: [SUPPORT.md](SUPPORT.md)
- Contribution guide: [CONTRIBUTING.md](CONTRIBUTING.md)
- Release checklist: [RELEASE.md](RELEASE.md)
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- CLI, MCP, and SDK reference: [docs/csegraph.md](docs/csegraph.md)
- Architecture reference: [docs/architecture.md](docs/architecture.md)

## Privacy and Local Files

CseGraph is local-first. Indexes are written under the target repository's
`.csegraph/` directory, while registry and daemon metadata use `~/.csegraph/`
for registered repository paths, database paths, daemon PID files, and logs.

No network request is required for normal indexing, retrieval, or MCP stdio. The
optional embeddings workflow can call an OpenAI-compatible endpoint only when
explicitly configured and allowed with
`CSEGRAPH_ALLOW_CLOUD_EMBEDDINGS`; that sends symbol text to the configured
endpoint.

## Development

```bash
pytest                              # Full test suite
pytest tests/unit/                  # Unit tests only
pytest tests/integration/           # Integration tests only
pytest -x -q                        # Stop on first failure, quiet
python -m compileall -q csegraph tools csegraph-vscode
csegraph --help
```

## License

CseGraph is released under the MIT License. See [LICENSE](LICENSE).
