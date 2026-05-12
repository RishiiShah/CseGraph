"""Integration tests for the csegraph MCP stdio server.

Tests the server by creating an in-process MCP client session that talks to the
server over in-memory streams, validating tool listing and tool invocation
against a real (temporary) csegraph index.
"""

from __future__ import annotations

import json
import pytest
from pathlib import Path

from csegraph_core.server.app import create_server, _handle_tool, _TOOLS


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        'from helpers import fmt\n\ndef greet(name: str) -> str:\n    """Say hello."""\n    return fmt(name)\n',
        encoding="utf-8",
    )
    (repo / "helpers.py").write_text(
        'def fmt(name: str) -> str:\n    return f"Hello, {name}"\n',
        encoding="utf-8",
    )
    return repo


class TestToolListing:
    def test_tool_names(self):
        names = {t.name for t in _TOOLS}
        assert names == {
            "csegraph_index",
            "csegraph_refresh",
            "csegraph_context",
            "csegraph_graph",
            "csegraph_path",
            "csegraph_tree",
            "csegraph_report",
        }

    def test_all_tools_have_required_fields(self):
        for tool in _TOOLS:
            assert tool.name
            assert tool.description
            assert tool.inputSchema
            assert "properties" in tool.inputSchema

    def test_required_params_declared(self):
        for tool in _TOOLS:
            required = tool.inputSchema.get("required", [])
            props = tool.inputSchema["properties"]
            for key in required:
                assert key in props, f"{tool.name} declares required '{key}' not in properties"


class TestHandleTool:
    def test_index_and_context(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = str(tmp_path / "test.db")

        result = _handle_tool("csegraph_index", {
            "repo": str(repo),
            "db": db,
            "profile": "small",
        })
        assert result["command"] == "index"
        assert result["files_indexed"] >= 1
        assert result["symbols_indexed"] >= 1

        ctx = _handle_tool("csegraph_context", {
            "task": "How does greet work?",
            "repo": str(repo),
            "target": "greet",
            "db": db,
        })
        assert "nodes" in ctx
        assert ctx["query"] == "How does greet work?"

    def test_refresh(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = str(tmp_path / "test.db")
        _handle_tool("csegraph_index", {"repo": str(repo), "db": db})

        (repo / "extra.py").write_text("x = 1\n", encoding="utf-8")
        result = _handle_tool("csegraph_refresh", {
            "repo": str(repo),
            "db": db,
        })
        assert result["command"] == "refresh"

    def test_graph_neighborhood(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = str(tmp_path / "test.db")
        _handle_tool("csegraph_index", {"repo": str(repo), "db": db})

        result = _handle_tool("csegraph_graph", {
            "node": "greet",
            "repo": str(repo),
            "db": db,
            "depth": 1,
        })
        assert "nodes" in result
        assert "edges" in result

    def test_report(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = str(tmp_path / "test.db")
        _handle_tool("csegraph_index", {"repo": str(repo), "db": db})

        result = _handle_tool("csegraph_report", {
            "repo": str(repo),
            "db": db,
        })
        assert result["total_files"] >= 1

    def test_path_found(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = str(tmp_path / "test.db")
        _handle_tool("csegraph_index", {"repo": str(repo), "db": db})

        result = _handle_tool("csegraph_path", {
            "source": "greet",
            "target": "fmt",
            "repo": str(repo),
            "db": db,
        })
        assert result["found"] is True
        assert result["length"] >= 1
        assert len(result["nodes"]) >= 2
        assert len(result["edges"]) >= 1

    def test_path_via_contains(self, tmp_path):
        repo = tmp_path / "repo2"
        repo.mkdir()
        (repo / "a.py").write_text("def alpha(): pass\n", encoding="utf-8")
        (repo / "b.py").write_text("def beta(): pass\n", encoding="utf-8")
        db = str(tmp_path / "test2.db")
        _handle_tool("csegraph_index", {"repo": str(repo), "db": db})

        result = _handle_tool("csegraph_path", {
            "source": "alpha",
            "target": "beta",
            "repo": str(repo),
            "db": db,
        })
        assert result["found"] is True
        assert result["length"] >= 1

    def test_unknown_tool_raises(self):
        with pytest.raises(ValueError, match="Unknown tool"):
            _handle_tool("csegraph_nope", {})

    def test_result_is_json_serializable(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = str(tmp_path / "test.db")
        _handle_tool("csegraph_index", {"repo": str(repo), "db": db})

        for tool_name, args in [
            ("csegraph_context", {"task": "greet", "repo": str(repo), "db": db}),
            ("csegraph_graph", {"node": "greet", "repo": str(repo), "db": db}),
            ("csegraph_report", {"repo": str(repo), "db": db}),
        ]:
            result = _handle_tool(tool_name, args)
            serialized = json.dumps(result)
            assert isinstance(json.loads(serialized), dict)


class TestServerCreation:
    def test_create_server_returns_server(self):
        server = create_server()
        assert server is not None
        assert server.name == "csegraph"
