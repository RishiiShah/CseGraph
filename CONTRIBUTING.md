# Contributing

## Setup

Use one editable Python package from the repository root:

```bash
python -m venv env
env/bin/python -m pip install --upgrade pip
env/bin/python -m pip install -e .
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

Run package-layout smoke tests:

```bash
env/bin/python -m pytest \
  tests/integration/test_package_layout.py \
  tests/integration/test_versions.py \
  tests/integration/test_cli.py \
  tests/integration/test_mcp_install.py \
  tests/integration/test_mcp_server.py \
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
- `learn.md`: brief learning backlog for what to add next.
- `RELEASE.md`, `SECURITY.md`, `CHANGELOG.md`: release hygiene.

## Generated Artifacts

Do not commit generated or local-only outputs:

- `.csegraph/`
- `.scratch/`
- `.vscode/`
- `build/`
- `dist/`
- `*.egg-info/`
- `*.vsix`
- `csegraph-vscode/node_modules/`
- `csegraph-vscode/out/`

CI enforces these source-first guardrails.
