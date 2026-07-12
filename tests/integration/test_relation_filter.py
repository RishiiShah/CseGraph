from __future__ import annotations

from pathlib import Path

from csegraph._core.graph.queries import GraphQueryService
from csegraph._core.index.services import IndexService
from csegraph._core.server.app import _handle_tool
from csegraph._core.server.tools import TOOLS


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "from helpers import fmt\n\ndef greet(name: str) -> str:\n    return fmt(name)\n",
        encoding="utf-8",
    )
    (repo / "helpers.py").write_text(
        "def fmt(name: str) -> str:\n    return name\n",
        encoding="utf-8",
    )
    db = str(repo / ".csegraph" / "index.db")
    IndexService(db).index(repo)
    return repo, db


def test_relation_filter_limits_graph_edges(tmp_path: Path):
    _, db = _repo(tmp_path)
    calls = GraphQueryService(db).neighborhood("greet", depth=1, relations=["calls"])
    imports = GraphQueryService(db).neighborhood("app.py", depth=1, relations=["imports"])

    assert {edge.relation for edge in calls.edges} == {"calls"}
    assert {edge.relation for edge in imports.edges} == {"imports"}


def test_graph_mcp_schema_and_dispatch_support_relations(tmp_path: Path):
    repo, _ = _repo(tmp_path)
    tool = next(tool for tool in TOOLS if tool.name == "csegraph_graph")
    assert tool.inputSchema["properties"]["relations"] == {
        "type": "array",
        "items": {"type": "string"},
    }

    payload = _handle_tool(
        "csegraph_graph",
        {"repo": str(repo), "node": "greet", "relations": ["calls"]},
    )
    assert {edge["relation"] for edge in payload["edges"]} == {"calls"}
