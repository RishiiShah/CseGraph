# Contributing

## Setup

Use one editable Python package from the repository root:

```bash
python -m venv env
env/bin/python -m pip install --upgrade pip
env/bin/python -m pip install -e ".[test,all]"
```

Optional local quality tools are grouped in the `dev` extra:

```bash
env/bin/python -m pip install -e ".[test,dev,all]"
env/bin/python -m pre_commit install
```

For the VS Code extension:

```bash
cd csegraph-vscode
npm ci
```

## Test Commands

Run the Python verification suite:

```bash
env/bin/python -m pytest tests/ -q
env/bin/python -m compileall -q csegraph tools csegraph-vscode
```

Run optional local quality checks:

```bash
env/bin/python -m ruff check .
env/bin/python -m mypy
env/bin/python -m coverage run -m pytest tests/ -q
env/bin/python -m coverage report
env/bin/python -m pre_commit run --all-files
```

The pre-commit configuration runs lightweight TOML/YAML validation and
conservative Ruff checks by default. Mypy and coverage are available as manual
hooks:

```bash
env/bin/python -m pre_commit run mypy --hook-stage manual --all-files
env/bin/python -m pre_commit run pytest-coverage --hook-stage manual --all-files
```

Run package-layout smoke tests:

```bash
env/bin/python -m pytest \
  tests/integration/test_package_layout.py \
  tests/integration/test_versions.py \
  tests/integration/test_cli.py \
  tests/integration/test_mcp_install.py \
  tests/integration/test_mcp_server.py \
  tests/integration/test_mcp_surface_guardrails.py \
  tests/integration/test_agent_workflow_benchmark.py \
  tests/integration/test_target_disambiguation.py \
  tests/integration/test_context_quality_corpus.py \
  -q
```

Run VS Code extension checks:

```bash
cd csegraph-vscode
npm audit --audit-level=high
npm run lint
npm run compile
npm run package
```

## Package Boundaries

- Publish one Python distribution: `csegraph`.
- Public Python imports are limited to `import csegraph` and names in
  `csegraph.__all__`.
- `csegraph._core` and `csegraph._cli` are private implementation namespaces.
- Do not add extra Python package roots or separate PyPI projects.
- Keep the VS Code extension as the root sibling project `csegraph-vscode/`.

## Documentation Boundaries

- `README.md`: public quickstart and surface overview.
- `CLAUDE.md` / `AGENTS.md`: agent-facing setup and repo rules.
- `docs/csegraph.md`: command and flag reference.
- `docs/architecture.md`: private-module architecture and data flow.
- `RELEASE.md`, `SECURITY.md`, `SUPPORT.md`, `CODE_OF_CONDUCT.md`,
  `CHANGELOG.md`: release and community hygiene.

## Generated Artifacts

Do not commit generated or local-only outputs:

- `.csegraph/`
- `.scratch/`
- `.vscode/`
- `.cursor/`
- `.gemini/`
- `.kiro/`
- `ref/`
- `build/`
- `dist/`
- `*.egg-info/`
- `*.vsix`
- `csegraph-vscode/node_modules/`
- `csegraph-vscode/out/`

CI enforces these source-first guardrails.
