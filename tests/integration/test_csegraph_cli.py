import json
import subprocess
import sys
from pathlib import Path


def _write_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "helpers.py").write_text(
        "\n".join(
            [
                "def clean_name(value: str) -> str:",
                "    return value.strip().lower()",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (root / "service.py").write_text(
        "\n".join(
            [
                "from helpers import clean_name",
                "",
                "def create_user(name: str) -> dict:",
                "    return {'name': clean_name(name)}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _run_cli(*args: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "csegraph_cli", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(proc.stdout)


def test_cli_json_contracts(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)

    indexed = _run_cli(
        "index",
        str(repo),
        "--json",
    )
    assert indexed["command"] == "index"
    assert indexed["profile"] == "medium"
    assert indexed["files_indexed"] == 2
    assert indexed["symbols_indexed"] == 2
    assert indexed["db_path"] == str(repo / ".csegraph" / "index.db")

    context = _run_cli(
        "context",
        "Implement create_user with clean_name",
        "--target",
        "create_user",
        "--repo",
        str(repo),
        "--json",
    )
    assert context["command"] == "context"
    assert context["target_node_id"] == "symbol::service.py::function::create_user"
    assert context["is_sufficient"] is True
    assert any(
        node["node_id"] == "symbol::helpers.py::function::clean_name"
        for node in context["context_nodes"]
    )

    graph = _run_cli(
        "graph",
        "symbol::service.py::function::create_user",
        "--repo",
        str(repo),
        "--depth",
        "1",
        "--json",
    )
    assert graph["command"] == "graph"
    assert graph["node_id"] == "symbol::service.py::function::create_user"
    assert any(edge["relation"] == "calls" for edge in graph["edges"])

    refreshed = _run_cli(
        "refresh",
        str(repo),
        "--json",
    )
    assert refreshed["command"] == "refresh"
    assert refreshed["changed_files"] == []
    assert refreshed["deleted_files"] == []


def test_legacy_explicit_db_flags_still_work(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "custom.db"
    _write_repo(repo)

    indexed = _run_cli(
        "index",
        "--repo",
        str(repo),
        "--db",
        str(db_path),
        "--profile",
        "small",
        "--json",
    )
    assert indexed["profile"] == "small"
    assert indexed["db_path"] == str(db_path)

    context = _run_cli(
        "context",
        "--db",
        str(db_path),
        "--task",
        "Implement create_user",
        "--target",
        "create_user",
        "--json",
    )
    assert context["target_node_id"] == "symbol::service.py::function::create_user"


def test_codegen_cli_json_contract(tmp_path):
    """Verify the codegen CLI command returns valid JSON with expected fields.

    Uses monkeypatching to stub out the LLM so we don't need a real model.
    """
    repo = tmp_path / "repo"
    _write_repo(repo)

    # Index first.
    _run_cli("index", str(repo), "--json")

    # Write a helper script that patches CodegenService to skip the LLM init
    # and return a canned result, then invokes the CLI.
    stub = tmp_path / "stub_codegen.py"
    stub.write_text(
        "\n".join(
            [
                "import sys, json",
                "from unittest.mock import patch, MagicMock",
                "from csegraph.core.models import CodegenResult",
                "from csegraph.cse.metrics import SufficiencyMetrics",
                "",
                "fake_result = CodegenResult(",
                '    command="codegen",',
                f'    db_path="{repo / ".csegraph" / "index.db"}",',
                f'    repo_root="{repo}",',
                '    profile="medium",',
                '    task="Implement create_user",',
                '    target_node_id="symbol::service.py::function::create_user",',
                '    model="stub-model",',
                '    generated_code="def create_user(name): return name",',
                "    is_sufficient=True,",
                "    metrics=SufficiencyMetrics(",
                "        dependency_completeness=1.0,",
                "        entity_coverage=1.0,",
                "        semantic_overlap=0.8,",
                "        model_confidence=0.9,",
                "    ),",
                '    context_nodes_used=["a"],',
                "    raw_code_nodes_used=[],",
                "    prompt_tokens=100,",
                "    completion_tokens=50,",
                "    elapsed_seconds=0.5,",
                ")",
                "",
                "mock_svc = MagicMock()",
                "mock_svc.generate.return_value = fake_result",
                "",
                "with patch('csegraph.codegen.service.CodegenService', return_value=mock_svc):",
                "    from csegraph_cli.main import main",
                "    sys.exit(main(sys.argv[1:]))",
                "",
            ]
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            str(stub),
            "codegen",
            "Implement create_user",
            "--repo",
            str(repo),
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(proc.stdout)

    assert result["command"] == "codegen"
    assert result["model"] == "stub-model"
    assert result["is_sufficient"] is True
    assert "generated_code" in result
    assert result["target_node_id"] == "symbol::service.py::function::create_user"
    assert result["prompt_tokens"] == 100
    assert result["completion_tokens"] == 50


def test_codegen_cli_output_flag(tmp_path):
    """Verify --output writes a .py file."""
    repo = tmp_path / "repo"
    _write_repo(repo)
    _run_cli("index", str(repo), "--json")

    out_py = tmp_path / "out.py"

    stub = tmp_path / "stub_codegen_out.py"
    stub.write_text(
        "\n".join(
            [
                "import sys, json",
                "from unittest.mock import patch, MagicMock",
                "from csegraph.core.models import CodegenResult",
                "from csegraph.cse.metrics import SufficiencyMetrics",
                "",
                "fake_result = CodegenResult(",
                '    command="codegen",',
                f'    db_path="{repo / ".csegraph" / "index.db"}",',
                f'    repo_root="{repo}",',
                '    profile="medium",',
                '    task="Implement create_user",',
                '    target_node_id="symbol::service.py::function::create_user",',
                '    model="stub-model",',
                '    generated_code="def create_user(name): return name",',
                "    is_sufficient=True,",
                "    metrics=SufficiencyMetrics(",
                "        dependency_completeness=1.0,",
                "        entity_coverage=1.0,",
                "        semantic_overlap=0.8,",
                "        model_confidence=0.9,",
                "    ),",
                '    context_nodes_used=["a"],',
                "    raw_code_nodes_used=[],",
                "    prompt_tokens=100,",
                "    completion_tokens=50,",
                "    elapsed_seconds=0.5,",
                f'    output_path="{out_py}",',
                ")",
                "",
                "mock_svc = MagicMock()",
                "mock_svc.generate.return_value = fake_result",
                "",
                "with patch('csegraph.codegen.service.CodegenService', return_value=mock_svc):",
                "    from csegraph_cli.main import main",
                "    sys.exit(main(sys.argv[1:]))",
                "",
            ]
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            str(stub),
            "codegen",
            "Implement create_user",
            "--repo",
            str(repo),
            "--output",
            str(out_py),
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(proc.stdout)
    assert result["command"] == "codegen"
    assert result["output_path"] == str(out_py)



def test_install_matrix_cli_works_without_sdk(tmp_path):
    """Sanity check that the CLI runs in an isolated venv with only csegraph-core
    + csegraph-cli installed (no `csegraph` SDK). The codegen subcommand must
    surface a friendly install-hint instead of importing the SDK."""
    repo_root = Path(__file__).resolve().parents[2]
    if not (repo_root / "packages" / "csegraph-core" / "pyproject.toml").exists():
        import pytest
        pytest.skip("csegraph-core package not present in this checkout")

    venv = tmp_path / "v"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    bin_dir = venv / ("Scripts" if sys.platform.startswith("win") else "bin")
    pip = bin_dir / ("pip.exe" if sys.platform.startswith("win") else "pip")
    csegraph_bin = bin_dir / ("csegraph.exe" if sys.platform.startswith("win") else "csegraph")

    subprocess.run(
        [str(pip), "install", "--quiet",
         "-e", str(repo_root / "packages" / "csegraph-core"),
         "-e", str(repo_root / "packages" / "csegraph-cli")],
        check=True,
    )

    # SDK must NOT be installed in this venv.
    listing = subprocess.run([str(pip), "list"], check=True, capture_output=True, text=True).stdout
    assert "csegraph-core" in listing
    assert "csegraph-cli" in listing
    sdk_lines = [
        line for line in listing.splitlines()
        if line.startswith("csegraph ") or line.split()[0:1] == ["csegraph"]
    ]
    assert sdk_lines == [], f"SDK should not be installed: {sdk_lines}"

    # index/refresh/context/graph must work.
    sample = tmp_path / "repo"
    _write_repo(sample)
    proc = subprocess.run(
        [str(csegraph_bin), "index", str(sample), "--json"],
        check=True, capture_output=True, text=True,
    )
    assert json.loads(proc.stdout)["files_indexed"] == 2

    # codegen must produce the friendly error (no SDK).
    proc = subprocess.run(
        [str(csegraph_bin), "codegen", "Implement create_user", "--repo", str(sample), "--json"],
        capture_output=True, text=True,
    )
    assert proc.returncode != 0
    err = json.loads(proc.stderr)
    assert "csegraph" in err.get("error", "").lower()
    assert "pip install csegraph" in err.get("error", "")
