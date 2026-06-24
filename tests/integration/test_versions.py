from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomlkit

import csegraph
import csegraph._cli
import csegraph._core

EXPECTED_VERSION = "1.8.1"


def _project_version(path: Path) -> str:
    pyproject = path / "pyproject.toml"
    if "tomllib" in globals():
        with pyproject.open("rb") as fh:
            return tomllib.load(fh)["project"]["version"]
    return tomlkit.parse(pyproject.read_text(encoding="utf-8"))["project"]["version"]


def test_all_package_versions_match_module_versions():
    repo_root = Path(__file__).resolve().parents[2]

    versions = {
        "root_pyproject": _project_version(repo_root),
        "csegraph._core": csegraph._core.__version__,
        "csegraph": csegraph.__version__,
        "csegraph._cli": csegraph._cli.__version__,
    }

    assert set(versions.values()) == {EXPECTED_VERSION}
