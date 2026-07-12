# Contributing to CseGraph

Thank you for your interest in contributing! This guide will help you set up the
project, run checks, and prepare a pull request.

## Development Setup

```bash
# Clone the repository
git clone https://github.com/RishiiShah/CseGraph.git
cd CseGraph

# Create and activate a virtual environment
python -m venv env
source env/bin/activate

# Install the package, tests, development tools, and benchmark tooling.
# Runtime language grammars are normal package dependencies in CseGraph 2.x.
python -m pip install --upgrade pip
python -m pip install -e ".[test,dev,benchmark]"

# Verify the setup
python -m pytest tests/ -q
```

On Windows PowerShell, activate the environment with:

```powershell
env\Scripts\Activate.ps1
```

## Running Tests

```bash
# All tests
python -m pytest tests/ -q

# Unit tests
python -m pytest tests/unit/ -q

# Integration tests
python -m pytest tests/integration/ -q

# Stop on the first failure
python -m pytest tests/ -x -q

# Single test file
python -m pytest tests/unit/test_python_parser.py -v

# With coverage
python -m coverage run -m pytest tests/ -q
python -m coverage report
```

## Linting and Type Checking

```bash
python -m ruff check .
python -m mypy
python -m compileall -q csegraph tools csegraph-vscode
```

To run every configured pre-commit check:

```bash
python -m pre_commit install
python -m pre_commit run --all-files
```

Mypy and coverage are manual pre-commit hooks:

```bash
python -m pre_commit run mypy --hook-stage manual --all-files
python -m pre_commit run pytest-coverage --hook-stage manual --all-files
```

## Code Style

- **Python version**: 3.10+
- **Line length**: 100 characters
- **Linter**: Ruff
- **Ruff rules**: `B`, `E9`, `F`, and `I`
- **Type checker**: Mypy
- **Imports**: Sorted by Ruff
- **Public API**: Export supported names through `csegraph.__all__`
- **SQL**: Use parameterized queries; never interpolate untrusted values

Implementation modules under `csegraph._core` and `csegraph._cli` are private.
Do not expose a new public API without tests and documentation.

## Making Changes

1. Fork the repository.
2. Create a branch: `git checkout -b feature/your-feature`.
3. Make a focused change.
4. Add or update tests.
5. Run `python -m pytest tests/ -q`.
6. Run `python -m ruff check .`.
7. Run `python -m mypy`.
8. Update documentation when behavior or public interfaces change.
9. Submit a pull request with a clear description and verification notes.

For large design changes or new public surfaces, open an issue before investing
in a full implementation.

## Project Structure

```text
csegraph/
  __init__.py                 # Public Python API
  _cli/                       # CLI parsing and terminal rendering
  _core/
    core/                     # Shared models and service contracts
    graph/                    # Focused graph and path queries
    index/                    # Repository indexing facade and focused internals
      writer.py              # Parsed-file persistence and graph writes
    languages/                # Parser registry and Tree-sitter grammars
    retrieval/                # Adaptive context, freshness, and budgeting
    server/                   # MCP server and tool handlers
    mcp_install.py            # Coding-agent configuration installer
    status.py                 # Index health and freshness reporting
csegraph-vscode/              # VS Code extension
docs/                         # User and architecture documentation
tests/
  unit/                       # Focused unit tests
  integration/                # End-to-end and contract tests
tools/                        # Maintainer utilities and focused benchmark package
  benchmarks/quality.py       # Benchmark corpus quality and completeness gates
```

## Adding Language Support

Built-in language support uses Tree-sitter grammar packages and private
`LanguageSpec` entries. CseGraph 2.x intentionally ships only Python,
JavaScript, and TypeScript support.

1. Add the grammar package as a runtime dependency in `pyproject.toml`.
2. Add a `LanguageSpec` and factory in
   `csegraph/_core/languages/treesitter/languages.py`.
3. Define extensions, symbol node types, call types, import extraction, and
   language-specific test conventions.
4. Add parser and registry coverage under `tests/unit/`.
5. Add integration tests when the language changes indexing, discovery,
   imports, dependency resolution, or context retrieval.
6. Update the language list in `README.md`.

Do not add a public parser plugin API unless the public contract, tests, and
documentation are updated in the same change.

## VS Code Extension

```bash
cd csegraph-vscode
npm ci

# Run checks
npm audit --audit-level=high
npm run lint
npm run compile

# Build and install a local VSIX
npm run package
code --install-extension csegraph-vscode-*.vsix --force
```

The extension is a thin UI around the CseGraph CLI. Graph logic should remain
in the Python package.

## Documentation

Keep documentation focused and in its intended location:

- `README.md`: public overview, installation, quick start, and public surface
- `CONTRIBUTING.md`: contributor workflow
- `docs/architecture.md`: internal architecture and data flow
- `docs/benchmarks.md`: benchmark tiers and evidence-output workflow
- `RELEASE.md`: release process

## Reporting Issues

Open an issue at
[github.com/RishiiShah/CseGraph/issues](https://github.com/RishiiShah/CseGraph/issues)
and include:

- Installed CseGraph version from
  `python -c "import csegraph; print(csegraph.__version__)"`
- Python version and operating system
- Installation method
- Steps to reproduce
- Command output or traceback
- A small reproduction repository or code sample when possible

Report exploitable vulnerabilities privately through GitHub Security Advisories,
not through a public issue.

## Generated Files

Do not commit local indexes, build outputs, editor configuration generated by
the installer, or dependency directories. This includes:

- `.csegraph/`
- `.scratch/`
- `.agents/`, `.codex/`, `.claude/`, `.cursor/`, `.gemini/`, `.kiro/`, `.vscode/`
- `.mcp.json`
- `build/`, `dist/`, and `*.egg-info/`
- `*.vsix`
- `csegraph-vscode/node_modules/`
- `csegraph-vscode/out/`

Benchmark reports and cloned sandbox repositories are also disposable local
outputs. Keep benchmark source definitions under `tools/benchmarks/`; write
reports under `benchmark_results/` only when running a benchmark locally or in
CI.

## License

By contributing, you agree that your contributions will be licensed under the
[MIT License](LICENSE).
