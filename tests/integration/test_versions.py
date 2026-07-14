import json
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

import csegraph
import csegraph._cli
import csegraph._core

EXPECTED_VERSION = "2.0.1"


def _project_version(path: Path) -> str:
    pyproject = path / "pyproject.toml"
    with pyproject.open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def test_all_package_versions_match_module_versions():
    repo_root = Path(__file__).resolve().parents[2]

    versions = {
        "root_pyproject": _project_version(repo_root),
        "csegraph._core": csegraph._core.__version__,
        "csegraph": csegraph.__version__,
        "csegraph._cli": csegraph._cli.__version__,
    }

    assert set(versions.values()) == {EXPECTED_VERSION}

    vscode_package = json.loads(
        (repo_root / "csegraph-vscode" / "package.json").read_text(encoding="utf-8")
    )
    assert vscode_package["version"] == EXPECTED_VERSION
