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


def test_v125_package_layout_and_versions():
    repo_root = Path(__file__).resolve().parents[2]

    root_project = _project_metadata(repo_root)
    sdk_project = _project_metadata(repo_root / "packages" / "csegraph")
    cli_project = _project_metadata(repo_root / "packages" / "csegraph-cli")
    codegen_project = _project_metadata(repo_root / "packages" / "csegraph-codegen")

    assert root_project["name"] == "csegraph-core"
    assert root_project["version"] == "1.3.0"
    assert root_project.get("dependencies", []) == []
    assert "import: csegraph_core" in root_project["description"]

    assert sdk_project["name"] == "csegraph"
    assert sdk_project["version"] == "1.3.0"
    assert sdk_project["dependencies"] == ["csegraph-core>=1.3.0"]

    assert cli_project["name"] == "csegraph-cli"
    assert cli_project["version"] == "1.3.0"
    assert cli_project["dependencies"] == ["csegraph-core>=1.3.0"]

    assert codegen_project["name"] == "csegraph-codegen"
    assert codegen_project["version"] == "1.3.0"
    assert codegen_project["dependencies"] == ["csegraph-core>=1.3.0"]

    assert (repo_root / "csegraph_core" / "__init__.py").exists()
    assert (repo_root / "packages" / "csegraph" / "csegraph" / "__init__.py").exists()
    assert (repo_root / "packages" / "csegraph-codegen" / "csegraph_codegen" / "__init__.py").exists()
    assert not (repo_root / "packages" / "csegraph-core").exists()
    assert not (repo_root / "packages" / "csegraph" / "csegraph" / "codegen").exists()


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
                "assert not hasattr(csegraph, 'CodegenService'); "
                "assert not hasattr(csegraph, 'CodegenResult'); "
                "import csegraph.languages.python.parser"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert sdk.returncode == 0


def test_install_matrix_codegen_addon_is_separate_from_sdk(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    venv = tmp_path / "v"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    bin_dir = venv / ("Scripts" if sys.platform.startswith("win") else "bin")
    pip = bin_dir / ("pip.exe" if sys.platform.startswith("win") else "pip")
    python = bin_dir / ("python.exe" if sys.platform.startswith("win") else "python")

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
            str(repo_root / "packages" / "csegraph-codegen"),
        ],
        check=True,
        env=_offline_pip_env(),
    )
    addon = subprocess.run(
        [
            str(python),
            "-c",
            (
                "from csegraph_codegen import CodegenResult, CodegenService; "
                "import importlib.util; "
                "assert importlib.util.find_spec('csegraph') is None"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert addon.returncode == 0
