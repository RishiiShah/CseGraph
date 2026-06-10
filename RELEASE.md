# Release Checklist

## Pre-Release Verification

Run from the repository root:

```bash
env/bin/python -m pip install -e ".[test]"
env/bin/python -m pytest tests/ -q
env/bin/python -m compileall -q csegraph tools csegraph-vscode
env/bin/csegraph index . --json
env/bin/python tools/csegraph_dev.py benchmark . --corpus benchmarks/context_quality/csegraph_self.json --json
```

Run from `csegraph-vscode/`:

```bash
npm ci
npm audit --audit-level=high
npm run lint
npm run compile
npm run package
```

## Python Distribution

Build the Python artifacts from the repository root:

```bash
env/bin/python -m pip install --upgrade build
env/bin/python -m build
```

Inspect the wheel before publishing:

```bash
env/bin/python - <<'PY'
from pathlib import Path
import tomllib
import zipfile

version = tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"]
wheel = next(Path("dist").glob("csegraph-*.whl"))
with zipfile.ZipFile(wheel) as archive:
    names = archive.namelist()
    metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
    metadata = archive.read(metadata_name).decode()
assert any(name.startswith("csegraph/") for name in names)
assert all(
    name.startswith(("csegraph/", f"csegraph-{version}.dist-info/"))
    for name in names
)
assert "Description-Content-Type: text/markdown" in metadata
assert "# csegraph" in metadata
PY
```

Publish to PyPI only from the GitHub release workflow using trusted publishing.

## VS Code Extension

Build and inspect the VSIX:

```bash
cd csegraph-vscode
npm ci
npm audit --audit-level=high
npm run lint
npm run compile
npm run package
```

Install locally before publishing:

```bash
VSIX=$(python -c "import json; print('csegraph-vscode-' + json.load(open('package.json'))['version'] + '.vsix')")
code --install-extension "$VSIX" --force
code --list-extensions --show-versions | grep csegraph.csegraph-vscode
```

Publish to the VS Code Marketplace only from the GitHub release workflow when
`VSCE_PAT` is configured.

## Post-Release Checks

- `pip install csegraph` installs the new version.
- `csegraph --help` works.
- `python -c "from csegraph import ContextService"` works.
- Only the `csegraph` distribution is installed for this project.
- VS Code lists `csegraph.csegraph-vscode` at the released version.
