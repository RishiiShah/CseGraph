from pathlib import Path
import os
import site
import subprocess
import sys
import tomllib


CORE_RUNTIME_DEPENDENCIES = [
    "mcp>=1.0.0,<2",
    "watchfiles>=1.0.0,<2",
    "tomlkit>=0.12.0,<1",
]


CORE_LANGUAGE_DEPENDENCIES = [
    "tree-sitter>=0.23",
    "tree-sitter-python>=0.23",
    "tree-sitter-typescript>=0.23",
    "tree-sitter-javascript>=0.23",
    "tree-sitter-go>=0.23",
    "tree-sitter-rust>=0.23",
    "tree-sitter-java>=0.23",
    "tree-sitter-c>=0.23",
    "tree-sitter-cpp>=0.23",
    "tree-sitter-ruby>=0.23",
    "tree-sitter-c-sharp>=0.23",
    "tree-sitter-kotlin>=0.23",
    "tree-sitter-groovy>=0.1.2",
    "tree-sitter-scala>=0.23",
    "tree-sitter-php>=0.23",
    "tree-sitter-swift>=0.7",
    "tree-sitter-lua>=0.2",
    "tree-sitter-zig>=0.1",
    "tree-sitter-powershell>=0.1",
    "tree-sitter-elixir>=0.3",
    "tree-sitter-objc>=0.23",
    "tree-sitter-julia>=0.23",
    "tree-sitter-verilog>=0.23",
    "tree-sitter-fortran>=0.6",
]


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
    assert root_project["version"] == "1.6.0"
    assert root_project["dependencies"] == CORE_RUNTIME_DEPENDENCIES + CORE_LANGUAGE_DEPENDENCIES
    assert set(root_project.get("optional-dependencies", {})) == {"test"}
    assert "import: csegraph_core" in root_project["description"]

    assert sdk_project["name"] == "csegraph"
    assert sdk_project["version"] == "1.6.0"
    assert sdk_project["dependencies"] == ["csegraph-core>=1.6.0"]

    assert cli_project["name"] == "csegraph-cli"
    assert cli_project["version"] == "1.6.0"
    assert cli_project["dependencies"] == ["csegraph-core>=1.6.0"]

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
        [
            str(pip),
            "install",
            "--quiet",
            "--no-index",
            "--no-build-isolation",
            "--no-deps",
            "-e",
            str(repo_root),
        ],
        check=True,
        env=_offline_pip_env(),
    )
    core_only = subprocess.run(
        [str(python), "-c", "import csegraph_core; import importlib.util; assert importlib.util.find_spec('csegraph') is None"],
        check=True,
        capture_output=True,
        env=_offline_pip_env(),
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
            "--no-deps",
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
        env=_offline_pip_env(),
        text=True,
    )
    assert sdk.returncode == 0


def test_cli_package_source_install_exposes_base_commands(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    venv = tmp_path / "v"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    bin_dir = venv / ("Scripts" if sys.platform.startswith("win") else "bin")
    pip = bin_dir / ("pip.exe" if sys.platform.startswith("win") else "pip")
    csegraph = bin_dir / ("csegraph.exe" if sys.platform.startswith("win") else "csegraph")

    subprocess.run(
        [
            str(pip),
            "install",
            "--quiet",
            "--no-index",
            "--no-build-isolation",
            "--no-deps",
            "-e",
            str(repo_root),
            "-e",
            str(repo_root / "packages" / "csegraph-cli"),
        ],
        check=True,
        env=_offline_pip_env(),
    )

    base_commands = [
        "index",
        "refresh",
        "context",
        "status",
        "postprocess",
        "install",
        "watch",
        "serve",
    ]
    for command in base_commands:
        proc = subprocess.run(
            [str(csegraph), command, "--help"],
            check=True,
            capture_output=True,
            env=_offline_pip_env(),
            text=True,
        )
        assert f"usage: csegraph {command}" in proc.stdout


def test_core_module_entrypoint_points_to_cli_package(tmp_path):
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
            "--no-deps",
            "-e",
            str(repo_root),
        ],
        check=True,
        env=_offline_pip_env(),
    )

    proc = subprocess.run(
        [str(python), "-m", "csegraph_core"],
        capture_output=True,
        env=_offline_pip_env(),
        text=True,
    )

    assert proc.returncode == 1
    assert "python -m csegraph_core` is not the CLI" in proc.stderr
    assert "pip install -e . -e packages/csegraph-cli/" in proc.stderr
    assert "python -m csegraph_cli <command>" in proc.stderr


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


def test_sdk_core_full_parity():
    import csegraph_core
    import csegraph

    core_all = set(csegraph_core.__all__)
    sdk_all = set(csegraph.__all__)

    assert core_all == sdk_all, (
        f"core-only: {sorted(core_all - sdk_all)}, sdk-only: {sorted(sdk_all - core_all)}"
    )

    for name in sorted(core_all):
        if name == "__version__":
            continue
        assert getattr(csegraph, name) is getattr(csegraph_core, name), (
            f"SDK re-export {name} is not the same object as core"
        )


def test_review_intelligence_sdk_exports():
    from csegraph import (
        TestGapService,
        TestGapResult,
        UntestedSymbol,
        CommunityCoverage,
        ReviewQuestionsService,
        ReviewQuestionsResult,
        ReviewQuestion,
        ReviewEvalService,
        ReviewEvalResult,
        RiskLevelMetrics,
    )
    import csegraph_core

    assert TestGapService is csegraph_core.TestGapService
    assert TestGapResult is csegraph_core.TestGapResult
    assert UntestedSymbol is csegraph_core.UntestedSymbol
    assert CommunityCoverage is csegraph_core.CommunityCoverage
    assert ReviewQuestionsService is csegraph_core.ReviewQuestionsService
    assert ReviewQuestionsResult is csegraph_core.ReviewQuestionsResult
    assert ReviewQuestion is csegraph_core.ReviewQuestion
    assert ReviewEvalService is csegraph_core.ReviewEvalService
    assert ReviewEvalResult is csegraph_core.ReviewEvalResult
    assert RiskLevelMetrics is csegraph_core.RiskLevelMetrics
