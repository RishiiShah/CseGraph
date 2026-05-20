from pathlib import Path

import pytest

import tree_sitter

from tests.conftest import run_cli


def _write_ts_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "utils.ts").write_text(
        "export function formatName(name: string): string {\n"
        "  return name.trim().toLowerCase();\n"
        "}\n",
        encoding="utf-8",
    )
    (root / "service.ts").write_text(
        "import { formatName } from './utils';\n\n"
        "export class UserService {\n"
        "  createUser(name: string): string {\n"
        "    return formatName(name);\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )


def test_index_typescript_files(tmp_path):
    repo = tmp_path / "repo"
    _write_ts_repo(repo)
    result = run_cli("index", str(repo), "--json")

    assert result["files_indexed"] == 2
    assert result["symbols_indexed"] >= 3
    assert result["edges_indexed"] >= 3


def test_context_retrieval_for_typescript(tmp_path):
    repo = tmp_path / "repo"
    _write_ts_repo(repo)
    run_cli("index", str(repo), "--json")

    result = run_cli(
        "context",
        "Implement createUser method",
        "--target", "createUser",
        "--repo", str(repo),
        "--json",
    )

    assert result["sufficiency"]["sufficient"] is True
    node_ids = [n["id"] for n in result["nodes"]]
    assert any("createUser" in nid for nid in node_ids)
    for node in result["nodes"]:
        assert node["language"] == "typescript"


def test_inspect_typescript_class(tmp_path):
    repo = tmp_path / "repo"
    _write_ts_repo(repo)
    run_cli("index", str(repo), "--json")

    result = run_cli(
        "inspect", "UserService",
        "--repo", str(repo),
        "--detail-level", "standard",
        "--json",
    )

    assert result["command"] == "inspect"
    assert "UserService" in result["target"]
    assert any(e["relation"] == "contains" for e in result["edges"])


def test_typescript_cross_file_call_edge(tmp_path):
    repo = tmp_path / "repo"
    _write_ts_repo(repo)
    run_cli("index", str(repo), "--json")

    result = run_cli(
        "inspect",
        "symbol::service.ts::method::UserService.createUser",
        "--repo", str(repo),
        "--depth", "1",
        "--detail-level", "standard",
        "--json",
    )

    call_edges = [e for e in result["edges"] if e["relation"] == "calls"]
    assert len(call_edges) >= 1
    targets = [e["target"] for e in call_edges]
    assert any("formatName" in t for t in targets)


def test_typescript_import_edge(tmp_path):
    repo = tmp_path / "repo"
    _write_ts_repo(repo)
    run_cli("index", str(repo), "--json")

    result = run_cli(
        "inspect", "file::service.ts",
        "--repo", str(repo),
        "--depth", "1",
        "--detail-level", "standard",
        "--json",
    )

    import_edges = [e for e in result["edges"] if e["relation"] == "imports"]
    assert len(import_edges) >= 1
    assert any("utils" in e["target"] for e in import_edges)


def test_mixed_python_and_typescript(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "app.ts").write_text(
        "export function greet(): string {\n  return 'hi';\n}\n",
        encoding="utf-8",
    )
    (repo / "main.py").write_text(
        "def main():\n    print('hello')\n",
        encoding="utf-8",
    )
    result = run_cli("index", str(repo), "--json")

    assert result["files_indexed"] == 2
    assert result["symbols_indexed"] == 2


def test_refresh_detects_typescript_changes(tmp_path):
    repo = tmp_path / "repo"
    _write_ts_repo(repo)
    run_cli("index", str(repo), "--json")

    (repo / "utils.ts").write_text(
        "export function formatName(name: string): string {\n"
        "  return name.trim();\n"
        "}\n\n"
        "export function newHelper(): void {}\n",
        encoding="utf-8",
    )

    result = run_cli("refresh", str(repo), "--json")

    assert "utils.ts" in result["changed_files"]
    assert result["files_indexed"] >= 1


def test_report_includes_typescript_symbols(tmp_path):
    repo = tmp_path / "repo"
    _write_ts_repo(repo)
    run_cli("index", str(repo), "--json")

    result = run_cli("report", str(repo), "--json")

    assert result["total_files"] == 2
    assert result["total_symbols"] >= 3


def test_graph_visual_export_with_typescript(tmp_path):
    repo = tmp_path / "repo"
    _write_ts_repo(repo)
    run_cli("index", str(repo), "--json")

    output = tmp_path / "graph.html"
    result = run_cli(
        "graph",
        "--repo", str(repo),
        "--output", str(output),
        "--json",
    )

    assert result["total_nodes"] >= 2
    content = output.read_text(encoding="utf-8")
    assert "UserService" in content
    assert "formatName" in content
