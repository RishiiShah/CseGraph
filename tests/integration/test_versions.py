from pathlib import Path
import tomllib

import csegraph
import csegraph_cli
import csegraph_core


EXPECTED_VERSION = "1.7.0"


def _project_version(path: Path) -> str:
    with (path / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def test_all_package_versions_match_module_versions():
    repo_root = Path(__file__).resolve().parents[2]

    versions = {
        "root_pyproject": _project_version(repo_root),
        "sdk_pyproject": _project_version(repo_root / "packages" / "csegraph"),
        "cli_pyproject": _project_version(repo_root / "packages" / "csegraph-cli"),
        "csegraph_core": csegraph_core.__version__,
        "csegraph": csegraph.__version__,
        "csegraph_cli": csegraph_cli.__version__,
    }

    assert set(versions.values()) == {EXPECTED_VERSION}
