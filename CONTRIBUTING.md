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

# Install the package, tests, development tools, and all language grammars
python -m pip install --upgrade pip
python -m pip install -e ".[test,dev,all]"

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
    graph/                    # SQLite graph storage and queries
    index/                    # Repository indexing pipeline
    languages/                # Parser registry and Tree-sitter grammars
    retrieval/                # Context selection and scoring
    server/                   # MCP server and tool handlers
    mcp_install.py            # Coding-agent configuration installer
    watch.py                  # File watching and incremental refresh
csegraph-vscode/              # VS Code extension
docs/                         # User and architecture documentation
tests/
  unit/                       # Focused unit tests
  integration/                # End-to-end and contract tests
tools/                        # Maintainer and benchmark utilities
```

## Adding Language Support

Built-in language support uses Tree-sitter grammar packages and declarative
`LanguageSpec` entries.

1. Add the grammar package as an optional dependency in `pyproject.toml`.
2. Add a `LanguageSpec` and factory in
   `csegraph/_core/languages/treesitter/languages.py`.
3. Define extensions, symbol node types, call types, import extraction, and
   language-specific test conventions.
4. Add parser coverage in `tests/unit/test_treesitter_languages.py`.
5. Add registry or integration tests when the language changes discovery,
   imports, or dependency resolution.
6. Update the language list in `README.md`.

For external integrations that do not belong in the built-in registry, the
public API also exposes `register_parser` and `register_tree_sitter_language`.

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

Install the documentation dependencies and build the site:

```bash
python -m pip install -e ".[docs]"
python -m mkdocs build --strict
```

Keep documentation in its intended location:

- `README.md`: public overview, installation, and quick start
- `CONTRIBUTING.md`: contributor workflow
- `docs/csegraph.md`: CLI, MCP, and SDK reference
- `docs/architecture.md`: internal architecture and data flow
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
- `.codex/`, `.claude/`, `.cursor/`, `.gemini/`, `.kiro/`, `.vscode/`
- `.mcp.json`
- `build/`, `dist/`, and `*.egg-info/`
- `*.vsix`
- `csegraph-vscode/node_modules/`
- `csegraph-vscode/out/`

## License

By contributing, you agree that your contributions will be licensed under the
[MIT License](LICENSE).
