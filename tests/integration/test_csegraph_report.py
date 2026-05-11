import json
import subprocess
import sys
from pathlib import Path


def _write_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "helpers.py").write_text(
        "def clean_name(value: str) -> str:\n    return value.strip().lower()\n",
        encoding="utf-8",
    )
    (root / "service.py").write_text(
        "from helpers import clean_name\n\n"
        "def create_user(name: str) -> dict:\n    return {'name': clean_name(name)}\n",
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


def _run_cli_text(*args: str) -> str:
    proc = subprocess.run(
        [sys.executable, "-m", "csegraph_cli", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout


def test_report_json_contract(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    _run_cli("index", str(repo), "--json")

    result = _run_cli("report", str(repo), "--json")

    assert result["command"] == "report"
    assert result["total_files"] == 2
    assert result["total_symbols"] >= 2
    assert result["total_edges"] >= 1
    assert result["parse_error_count"] == 0
    assert isinstance(result["node_counts"], dict)
    assert "file" in result["node_counts"]
    assert isinstance(result["edge_counts"], dict)
    assert isinstance(result["god_nodes"], list)
    assert isinstance(result["knowledge_gaps"], list)
    assert isinstance(result["surprising_connections"], list)
    assert isinstance(result["suggested_questions"], list)


def test_report_json_god_nodes_are_sorted_by_degree(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    _run_cli("index", str(repo), "--json")

    result = _run_cli("report", str(repo), "--json")

    degrees = [n["degree"] for n in result["god_nodes"]]
    assert degrees == sorted(degrees, reverse=True)


def test_report_json_is_deterministic(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    _run_cli("index", str(repo), "--json")

    run1 = _run_cli("report", str(repo), "--json")
    run2 = _run_cli("report", str(repo), "--json")

    del run1["db_path"], run1["repo_root"]
    del run2["db_path"], run2["repo_root"]
    assert run1 == run2


def test_report_default_output_is_markdown(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    _run_cli("index", str(repo), "--json")

    output = _run_cli_text("report", str(repo))

    assert "# csegraph report" in output
    assert "## Corpus Check" in output
    assert "## Summary" in output
    assert "## God Nodes" in output
    assert "Files" in output
    assert "Symbols" in output
    assert "Edges" in output


def test_report_knowledge_gaps_contain_low_degree_symbols(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)

    (repo / "orphan.py").write_text(
        "def isolated_function():\n    return 42\n",
        encoding="utf-8",
    )

    _run_cli("index", str(repo), "--json")
    result = _run_cli("report", str(repo), "--json")

    gap_names = [n["name"] for n in result["knowledge_gaps"]]
    assert "isolated_function" in gap_names


def test_report_with_custom_db(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "custom.db"
    _write_repo(repo)
    _run_cli("index", "--repo", str(repo), "--db", str(db_path), "--json")

    result = _run_cli("report", "--db", str(db_path), "--json")

    assert result["command"] == "report"
    assert result["total_files"] == 2
