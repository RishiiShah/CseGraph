from pathlib import Path
import os
import site
import subprocess
import sys
import tomllib


def _project_metadata(path: Path) -> dict:
    with (path / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["project"]


def _offline_pip_env() -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(site.getsitepackages())
    return env


def test_v140_package_layout_and_versions():
    repo_root = Path(__file__).resolve().parents[2]

    root_project = _project_metadata(repo_root)
    sdk_project = _project_metadata(repo_root / "packages" / "csegraph")
    cli_project = _project_metadata(repo_root / "packages" / "csegraph-cli")

    assert root_project["name"] == "csegraph-core"
    assert root_project["version"] == "1.5.0"
    assert root_project.get("dependencies", []) == []
    assert "import: csegraph_core" in root_project["description"]

    assert sdk_project["name"] == "csegraph"
    assert sdk_project["version"] == "1.5.0"
    assert sdk_project["dependencies"] == ["csegraph-core>=1.5.0"]

    assert cli_project["name"] == "csegraph-cli"
    assert cli_project["version"] == "1.5.0"
    assert cli_project["dependencies"] == ["csegraph-core>=1.5.0"]

    assert (repo_root / "csegraph_core" / "__init__.py").exists()
    assert (repo_root / "packages" / "csegraph" / "csegraph" / "__init__.py").exists()
    assert not (repo_root / "packages" / "csegraph-core").exists()
    removed_addon = "code" + "gen"
    assert not (repo_root / "packages" / "csegraph" / "csegraph" / removed_addon).exists()
    assert not (repo_root / "packages" / f"csegraph-{removed_addon}").exists()


def test_install_matrix_sdk_is_separate_from_core(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    venv = tmp_path / "v"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    bin_dir = venv / ("Scripts" if sys.platform.startswith("win") else "bin")
    pip = bin_dir / ("pip.exe" if sys.platform.startswith("win") else "pip")
    python = bin_dir / ("python.exe" if sys.platform.startswith("win") else "python")

    subprocess.run(
        [str(pip), "install", "--quiet", "--no-index", "--no-build-isolation", "-e", str(repo_root)],
        check=True,
        env=_offline_pip_env(),
    )
    core_only = subprocess.run(
        [str(python), "-c", "import csegraph_core; import importlib.util; assert importlib.util.find_spec('csegraph') is None"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert core_only.returncode == 0

    subprocess.run(
        [
            str(pip),
            "install",
            "--quiet",
            "--no-index",
            "--no-build-isolation",
            "-e",
            str(repo_root),
            "-e",
            str(repo_root / "packages" / "csegraph"),
        ],
        check=True,
        env=_offline_pip_env(),
    )
    sdk = subprocess.run(
        [
            str(python),
            "-c",
            (
                "import csegraph; "
                "from csegraph import ContextService; "
                "import importlib.util; "
                "assert ContextService is not None; "
                "assert importlib.util.find_spec('csegraph.languages') is None"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert sdk.returncode == 0


def test_status_and_postprocess_exports():
    from csegraph_core import StatusService, StatusResult, PostprocessService, PostprocessResult
    from csegraph import StatusService as SDKStatusService
    from csegraph import PostprocessService as SDKPostprocessService
    from csegraph import StatusResult as SDKStatusResult
    from csegraph import PostprocessResult as SDKPostprocessResult

    assert StatusService is SDKStatusService
    assert PostprocessService is SDKPostprocessService
    assert StatusResult is SDKStatusResult
    assert PostprocessResult is SDKPostprocessResult
