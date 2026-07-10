from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path

import pytest

from csegraph._core.core.serializer import to_dict
from csegraph._core.graph.queries import GraphQueryService
from csegraph._core.index.services import IndexService


def _index_python_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text(
        "from helpers import format_name\n\n"
        "def greet(name: str) -> str:\n"
        "    return format_name(name)\n",
        encoding="utf-8",
    )
    (repo / "helpers.py").write_text(
        "def format_name(name: str) -> str:\n"
        "    return name.title()\n\n"
        "def unused() -> None:\n"
        "    pass\n",
        encoding="utf-8",
    )
    db_path = str(tmp_path / "index.db")
    IndexService(db_path).index(repo)
    return repo, db_path


def test_graph_queries_canonical_v12_entities(tmp_path: Path) -> None:
    _, db_path = _index_python_repo(tmp_path)

    with sqlite3.connect(db_path) as conn:
        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'nodes'"
            ).fetchone()
            is None
        )

    result = GraphQueryService(db_path).neighborhood("main.py")
    payload = to_dict(result)

    assert payload["schema_version"] == "csegraph-graph-v2"
    assert payload["target"] == "file::main.py"
    assert {node["id"] for node in payload["nodes"]} == {
        "file::helpers.py",
        "file::main.py",
    }
    assert payload["edges"] == [
        {
            "source": "file::main.py",
            "target": "file::helpers.py",
            "relation": "imports",
        }
    ]
    assert all("line_range" not in node for node in payload["nodes"])
    assert {
        "command",
        "db_path",
        "repo_root",
        "detail_level",
        "relations_filter",
        "confidence_breakdown",
        "hubs_skipped",
    }.isdisjoint(payload)


def test_path_queries_canonical_v12_symbols(tmp_path: Path) -> None:
    _, db_path = _index_python_repo(tmp_path)

    result = GraphQueryService(db_path).shortest_path("greet", "format_name")
    payload = to_dict(result)

    assert payload["schema_version"] == "csegraph-path-v2"
    assert payload["found"] is True
    assert payload["length"] == 1
    assert [node["name"] for node in payload["nodes"]] == ["greet", "format_name"]
    assert payload["edges"] == [
        {
            "source": "symbol::main.py::function::greet",
            "target": "symbol::helpers.py::function::format_name",
            "relation": "calls",
        }
    ]
    assert {
        "command",
        "db_path",
        "repo_root",
        "detail_level",
        "relations_filter",
        "confidence_breakdown",
        "hubs_skipped",
    }.isdisjoint(payload)


def test_path_not_found_omits_empty_and_default_fields(tmp_path: Path) -> None:
    _, db_path = _index_python_repo(tmp_path)

    payload = to_dict(GraphQueryService(db_path).shortest_path("greet", "unused"))

    assert payload == {
        "schema_version": "csegraph-path-v2",
        "source": "symbol::main.py::function::greet",
        "target": "symbol::helpers.py::function::unused",
        "found": False,
        "summary": "No path: 'greet' ↛ 'unused'.",
    }


def test_graph_path_have_no_detail_level_public_behavior(tmp_path: Path) -> None:
    _, db_path = _index_python_repo(tmp_path)
    service = GraphQueryService(db_path)

    assert "detail_level" not in inspect.signature(service.neighborhood).parameters
    assert "detail_level" not in inspect.signature(service.shortest_path).parameters
    with pytest.raises(ValueError, match="indexed file or symbol"):
        service.neighborhood(".")
