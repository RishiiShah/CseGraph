from pathlib import Path
import json
import os
import site
import subprocess
import sys

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomlkit


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


def _pyproject_data(path: Path) -> dict:
    pyproject = path / "pyproject.toml"
    if "tomllib" in globals():
        with pyproject.open("rb") as fh:
            return tomllib.load(fh)
    return tomlkit.parse(pyproject.read_text(encoding="utf-8"))


def _project_metadata(path: Path) -> dict:
    return _pyproject_data(path)["project"]


def _offline_pip_env() -> dict:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    return env


def _create_test_venv(path: Path) -> None:
    subprocess.run([sys.executable, "-m", "venv", str(path)], check=True)
    child_site_packages = Path(
        subprocess.check_output(
        [
            str(path / ("Scripts" if sys.platform.startswith("win") else "bin") / ("python.exe" if sys.platform.startswith("win") else "python")),
            "-c",
            "import site; print(site.getsitepackages()[0])",
        ],
        text=True,
    ).strip()
    )
    parent_site_packages = Path(site.getsitepackages()[0])
    excluded_prefixes = (
        "pip",
        "csegraph",
        "csegraph_",
        "__editable__.csegraph",
        "__editable___csegraph",
    )
    for entry in parent_site_packages.iterdir():
        if entry.name.startswith(excluded_prefixes):
            continue
        target = child_site_packages / entry.name
        if target.exists():
            continue
        target.symlink_to(entry)


def test_one_distribution_package_layout_and_versions():
    repo_root = Path(__file__).resolve().parents[2]

    pyproject = _pyproject_data(repo_root)
    root_project = pyproject["project"]

    assert root_project["name"] == "csegraph"
    assert root_project["version"] == "1.7.1"
    assert root_project["readme"] == "README.md"
    assert root_project["requires-python"] == ">=3.10"
    assert root_project["dependencies"] == CORE_RUNTIME_DEPENDENCIES + CORE_LANGUAGE_DEPENDENCIES
    assert set(root_project.get("optional-dependencies", {})) == {"test", "dev", "embeddings"}
    assert "context engine" in root_project["description"]
    assert root_project["scripts"] == {"csegraph": "csegraph._cli.main:main"}
    classifiers = set(root_project["classifiers"])
    assert "Typing :: Typed" in classifiers
    assert {
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
    }.issubset(classifiers)
    assert pyproject["tool"]["setuptools"]["package-data"]["csegraph"] == ["py.typed"]
    assert root_project["urls"] == {
        "Repository": "https://github.com/RishiiShah/CseGraph",
        "Issues": "https://github.com/RishiiShah/CseGraph/issues",
        "Documentation": "https://github.com/RishiiShah/CseGraph/tree/main/docs",
        "Changelog": "https://github.com/RishiiShah/CseGraph/blob/main/CHANGELOG.md",
    }

    assert (repo_root / "csegraph" / "__init__.py").exists()
    assert (repo_root / "csegraph" / "py.typed").read_text(encoding="utf-8") == ""
    assert (repo_root / "csegraph" / "_core" / "__init__.py").exists()
    assert (repo_root / "csegraph" / "_cli" / "__init__.py").exists()
    assert (repo_root / "csegraph-vscode" / "package.json").exists()
    assert not (repo_root / "csegraph._core").exists()


def test_root_install_exposes_cli_sdk_and_private_modules(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    venv = tmp_path / "v"
    _create_test_venv(venv)
    bin_dir = venv / ("Scripts" if sys.platform.startswith("win") else "bin")
    pip = bin_dir / ("pip.exe" if sys.platform.startswith("win") else "pip")
    python = bin_dir / ("python.exe" if sys.platform.startswith("win") else "python")
    csegraph_bin = bin_dir / ("csegraph.exe" if sys.platform.startswith("win") else "csegraph")

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
    import_check = subprocess.run(
        [
            str(python),
            "-c",
            (
                "import importlib.util; "
                "import csegraph; "
                "import csegraph._core; "
                "import csegraph._cli; "
                "from csegraph import ContextService; "
                "assert ContextService is not None; "
                "assert importlib.util.find_spec('csegraph.languages') is None"
            ),
        ],
        check=True,
        capture_output=True,
        env=_offline_pip_env(),
        text=True,
    )
    assert import_check.returncode == 0

    help_proc = subprocess.run(
        [str(csegraph_bin), "--help"],
        check=True,
        capture_output=True,
        env=_offline_pip_env(),
        text=True,
    )
    assert "usage: csegraph" in help_proc.stdout

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
            [str(csegraph_bin), command, "--help"],
            check=True,
            capture_output=True,
            env=_offline_pip_env(),
            text=True,
        )
        assert f"usage: csegraph {command}" in proc.stdout


def test_private_core_module_entrypoint_points_to_public_cli(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    venv = tmp_path / "v"
    _create_test_venv(venv)
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
        [str(python), "-m", "csegraph._core"],
        capture_output=True,
        env=_offline_pip_env(),
        text=True,
    )

    assert proc.returncode == 1
    assert "python -m csegraph._core` is not the CLI" in proc.stderr
    assert "pip install csegraph" in proc.stderr
    assert "python -m csegraph._cli <command>" in proc.stderr


def test_status_and_postprocess_exports():
    from csegraph._core import StatusService, StatusResult, PostprocessService, PostprocessResult
    from csegraph import StatusService as SDKStatusService
    from csegraph import PostprocessService as SDKPostprocessService
    from csegraph import StatusResult as SDKStatusResult
    from csegraph import PostprocessResult as SDKPostprocessResult

    assert StatusService is SDKStatusService
    assert PostprocessService is SDKPostprocessService
    assert StatusResult is SDKStatusResult
    assert PostprocessResult is SDKPostprocessResult


def test_sdk_exports_context_engine_facade_only():
    import csegraph._core as core
    import csegraph

    sdk_all = set(csegraph.__all__)
    expected = {
        "__version__",
        "ContextNode",
        "ContextResult",
        "ContextService",
        "GraphEdgeView",
        "GraphNodeView",
        "GraphQueryService",
        "GraphResult",
        "IndexResult",
        "IndexService",
        "KeyEntity",
        "MinimalResult",
        "MinimalService",
        "NextToolSuggestion",
        "PathEdge",
        "PathResult",
        "PathStep",
        "POSTPROCESS_LEVELS",
        "PostprocessResult",
        "PostprocessService",
        "PROFILES",
        "ProfileConfig",
        "RefreshResult",
        "RefreshService",
        "StatusResult",
        "StatusService",
        "SufficiencyMetrics",
        "SufficiencyResult",
        "VALID_REASONS",
        "get_profile",
        "load_profile",
        "to_dict",
    }

    assert sdk_all == expected

    for name in sorted(sdk_all):
        if name == "__version__":
            continue
        assert getattr(csegraph, name) is getattr(core, name), (
            f"SDK re-export {name} is not the same object as core"
        )


def test_diagnostic_services_are_module_path_only():
    import csegraph._core as core
    import csegraph
    from csegraph._core.benchmark import BenchmarkService
    from csegraph._core.graph.test_gaps import TestGapService
    from csegraph._core.graph.review_questions import ReviewQuestionsService

    assert BenchmarkService is not None
    assert TestGapService is not None
    assert ReviewQuestionsService is not None
    private_names = {
        "ArchitectureService",
        "BenchmarkService",
        "ChangeDetectionService",
        "EmbeddingService",
        "FlowService",
        "ReportService",
        "ResolverService",
        "ReviewEvalService",
        "ReviewQuestionsService",
        "TestGapService",
        "VulnerabilityService",
    }
    for name in private_names:
        assert not hasattr(csegraph, name)
        assert not hasattr(core, name)


def test_source_first_package_guard():
    repo_root = Path(__file__).resolve().parents[2]
    publishable_pyprojects = sorted(
        path.relative_to(repo_root).as_posix()
        for path in repo_root.rglob("pyproject.toml")
        if "csegraph-vscode" not in path.relative_to(repo_root).as_posix()
        and ".scratch/" not in path.relative_to(repo_root).as_posix()
    )
    assert publishable_pyprojects == ["pyproject.toml"]

    tracked = subprocess.check_output(
        ["git", "-C", str(repo_root), "ls-files"],
        text=True,
    ).splitlines()
    forbidden_suffixes = (
        ".egg-info/PKG-INFO",
        ".egg-info/SOURCES.txt",
        ".pyc",
        ".pyo",
        ".db",
        ".sqlite",
        ".vsix",
    )
    forbidden_prefixes = (
        ".cursor/",
        ".csegraph/",
        ".gemini/",
        ".kiro/",
        ".scratch/",
        ".vscode/",
        "build/",
        "csegraph-vscode/node_modules/",
        "csegraph-vscode/out/",
        "dist/",
    )
    forbidden_exact = {
        "build.py",
        "scripts/verify_binary.py",
    }
    violations = [
        path
        for path in tracked
        if (repo_root / path).exists()
        and (
            path in forbidden_exact
            or path.startswith(forbidden_prefixes)
            or path.endswith(forbidden_suffixes)
        )
    ]
    assert violations == []


def test_release_hardening_files_and_vscode_audit_override():
    repo_root = Path(__file__).resolve().parents[2]

    expected_files = [
        ".github/workflows/ci.yml",
        ".github/workflows/release.yml",
        "SECURITY.md",
        "CODE_OF_CONDUCT.md",
        "CONTRIBUTING.md",
        "CHANGELOG.md",
        "RELEASE.md",
        "SUPPORT.md",
        "docs/architecture.md",
        "docs/csegraph.md",
    ]
    for rel_path in expected_files:
        assert (repo_root / rel_path).exists(), rel_path

    package_json = json.loads((repo_root / "csegraph-vscode" / "package.json").read_text())
    assert package_json["overrides"] == {"tmp": "^0.2.6"}

    lock = json.loads((repo_root / "csegraph-vscode" / "package-lock.json").read_text())
    tmp_version = lock["packages"]["node_modules/tmp"]["version"]
    major, minor, patch = (int(part) for part in tmp_version.split("."))
    assert (major, minor, patch) >= (0, 2, 6)
