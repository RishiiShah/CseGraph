import json
import os
import site
import sqlite3
import subprocess
import sys
from pathlib import Path

from csegraph_core.retrieval.constants import VALID_REASONS


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


def _offline_pip_env() -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(site.getsitepackages())
    return env


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
    assert context["query"] == "Implement create_user with clean_name"
    assert context["target"] == "symbol::service.py::function::create_user"
    assert context["total_estimated_tokens"] == context["estimated_tokens"]
    assert context["sufficiency"]["sufficient"] is context["is_sufficient"]
    assert context["sufficiency"]["metrics"] == context["metrics"]
    assert context["sufficiency"]["thresholds"] == context["thresholds"]
    assert context["target_node_id"] == "symbol::service.py::function::create_user"
    assert context["is_sufficient"] is True
    assert [node["id"] for node in context["nodes"]] == [
        node["node_id"] for node in context["context_nodes"]
    ]
    assert any(
        node["node_id"] == "symbol::helpers.py::function::clean_name"
        for node in context["context_nodes"]
    )
    assert context["estimated_tokens"] >= 1
    nodes_by_id = {node["node_id"]: node for node in context["context_nodes"]}
    canonical_by_id = {node["id"]: node for node in context["nodes"]}
    target_node = canonical_by_id["symbol::service.py::function::create_user"]
    helper_node = canonical_by_id["symbol::helpers.py::function::clean_name"]
    assert target_node["path"] == "service.py"
    assert target_node["line_range"] == [3, 4]
    assert "target" in target_node["reason"]
    assert "direct_call" in helper_node["reason"]
    assert all(
        reason in VALID_REASONS
        for node in context["nodes"]
        for reason in node["reason"]
    )
    assert all("expanded-from-" not in reason for node in context["nodes"] for reason in node["reason"])
    assert all("explanation" not in node for node in context["nodes"])
    assert "source_text" in nodes_by_id["symbol::service.py::function::create_user"]
    assert "def create_user(name: str) -> dict:" in nodes_by_id["symbol::service.py::function::create_user"]["source_text"]
    assert "def clean_name(value: str) -> str:" in nodes_by_id["symbol::helpers.py::function::clean_name"]["source_text"]
    assert nodes_by_id["symbol::service.py::function::create_user"]["estimated_tokens"] >= 1

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


def test_context_cli_source_controls_and_token_budget(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    _run_cli("index", str(repo), "--json")

    compact = _run_cli(
        "context",
        "Implement create_user",
        "--target",
        "create_user",
        "--repo",
        str(repo),
        "--include-source",
        "never",
        "--json",
    )
    assert compact["estimated_tokens"] == sum(
        node["estimated_tokens"] for node in compact["context_nodes"]
    )
    assert all(node["source_text"] is None for node in compact["context_nodes"])

    budgeted = _run_cli(
        "context",
        "Implement create_user",
        "--target",
        "create_user",
        "--repo",
        str(repo),
        "--include-source",
        "always",
        "--max-tokens",
        "20",
        "--json",
    )
    assert budgeted["estimated_tokens"] <= 20
    budgeted_nodes = {node["node_id"]: node for node in budgeted["context_nodes"]}
    assert "symbol::service.py::function::create_user" in budgeted_nodes
    helper = budgeted_nodes.get("symbol::helpers.py::function::clean_name")
    assert helper is None or helper["source_text"] is None


def test_context_config_overrides_thresholds(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    _run_cli("index", str(repo), "--json")

    config_file = tmp_path / "csegraph.json"
    config_file.write_text(
        json.dumps({"dep_threshold": 0.65, "confidence_threshold": 0.55}),
        encoding="utf-8",
    )

    context = _run_cli(
        "context",
        "Implement create_user with clean_name",
        "--target",
        "create_user",
        "--repo",
        str(repo),
        "--config",
        str(config_file),
        "--json",
    )
    assert context["thresholds"]["dependency_completeness"] == 0.65
    assert context["thresholds"]["model_confidence"] == 0.55
    assert "semantic_overlap_relaxed" in context["thresholds"]


def test_context_cli_explain_and_markdown_format(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    _run_cli("index", str(repo), "--json")

    explained = _run_cli(
        "context",
        "Implement create_user",
        "--target",
        "create_user",
        "--repo",
        str(repo),
        "--explain",
        "--format",
        "json",
    )
    assert all("explanation" in node for node in explained["nodes"])
    helper = next(
        node
        for node in explained["nodes"]
        if node["id"] == "symbol::helpers.py::function::clean_name"
    )
    assert "directly called by the target" in helper["explanation"]

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "csegraph_cli",
            "context",
            "Implement create_user",
            "--target",
            "create_user",
            "--repo",
            str(repo),
            "--format",
            "markdown",
            "--explain",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "# csegraph context" in proc.stdout
    assert "Query: Implement create_user" in proc.stdout
    assert "Reasons: target" in proc.stdout
    assert "Included because" in proc.stdout
    assert "```python" in proc.stdout


def test_context_cli_json_markdown_conflict_fails_clearly(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    _run_cli("index", str(repo), "--json")

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "csegraph_cli",
            "context",
            "Implement create_user",
            "--target",
            "create_user",
            "--repo",
            str(repo),
            "--json",
            "--format",
            "markdown",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    err = json.loads(proc.stderr)
    assert err["error_code"] == "invalid_cli_options"
    assert "--json cannot be combined with --format markdown" in err["error"]


def test_context_cli_unsupported_schema_returns_structured_error(tmp_path):
    db_path = tmp_path / "future.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta(key, value) VALUES('schema_version', 'csegraph-sqlite-v999');
            """
        )

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "csegraph_cli",
            "context",
            "Implement create_user",
            "--db",
            str(db_path),
            "--json",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    err = json.loads(proc.stderr)
    assert err == {
        "error": "Unsupported csegraph index schema",
        "error_code": "unsupported_schema",
        "hint": "Rebuild the index or install a compatible csegraph-core version.",
    }


def test_cli_help_lists_only_product_commands():
    proc = subprocess.run(
        [sys.executable, "-m", "csegraph_cli", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "index" in proc.stdout
    assert "refresh" in proc.stdout
    assert "context" in proc.stdout
    assert "graph" in proc.stdout
    removed_command = "code" + "gen"
    assert removed_command not in proc.stdout


def test_install_matrix_cli_works_without_sdk(tmp_path):
    """CLI should run with root csegraph-core + csegraph-cli, no SDK package."""
    repo_root = Path(__file__).resolve().parents[2]
    if not (repo_root / "pyproject.toml").exists():
        import pytest
        pytest.skip("root csegraph-core package not present in this checkout")

    venv = tmp_path / "v"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    bin_dir = venv / ("Scripts" if sys.platform.startswith("win") else "bin")
    pip = bin_dir / ("pip.exe" if sys.platform.startswith("win") else "pip")
    csegraph_bin = bin_dir / ("csegraph.exe" if sys.platform.startswith("win") else "csegraph")

    subprocess.run(
        [str(pip), "install", "--quiet", "--no-index", "--no-build-isolation",
         "-e", str(repo_root),
         "-e", str(repo_root / "packages" / "csegraph-cli")],
        check=True,
        env=_offline_pip_env(),
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
    proc = subprocess.run(
        [
            str(csegraph_bin),
            "context",
            "Implement create_user",
            "--target",
            "create_user",
            "--repo",
            str(sample),
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(proc.stdout)["target_node_id"] == "symbol::service.py::function::create_user"
    proc = subprocess.run(
        [
            str(csegraph_bin),
            "graph",
            "symbol::service.py::function::create_user",
            "--repo",
            str(sample),
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(proc.stdout)["command"] == "graph"
    proc = subprocess.run(
        [str(csegraph_bin), "refresh", str(sample), "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(proc.stdout)["command"] == "refresh"
