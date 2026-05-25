"""Integration tests for the csegraph MCP stdio server.

Tests the server by creating an in-process MCP client session that talks to the
server over in-memory streams, validating tool listing and tool invocation
against a real (temporary) csegraph index.
"""

from __future__ import annotations

import json
import asyncio
import subprocess
import pytest
from pathlib import Path

from mcp.types import ListPromptsRequest, ListToolsRequest

from csegraph_core.server.app import (
    CORE_TOOL_NAMES,
    create_server,
    _handle_prompt,
    _handle_tool,
    _PROMPTS,
    _TOOLS,
)


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


async def _listed_tool_names(server) -> list[str]:
    result = await server.request_handlers[ListToolsRequest](ListToolsRequest())
    return [tool.name for tool in result.root.tools]


async def _listed_prompt_names(server) -> list[str]:
    result = await server.request_handlers[ListPromptsRequest](ListPromptsRequest())
    return [prompt.name for prompt in result.root.prompts]


class TestToolListing:
    def test_tool_names(self):
        names = {t.name for t in _TOOLS}
        assert names == {
            "csegraph_index",
            "csegraph_refresh",
            "csegraph_minimal",
            "csegraph_context",
            "csegraph_graph",
            "csegraph_path",
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

    def test_context_tool_declares_detail_level(self):
        context_tool = next(tool for tool in _TOOLS if tool.name == "csegraph_context")
        detail_level = context_tool.inputSchema["properties"]["detail_level"]

        assert detail_level["enum"] == ["auto", "minimal", "standard", "full"]
        assert detail_level["default"] == "auto"


class TestPromptListing:
    def test_prompt_names(self):
        names = {prompt.name for prompt in _PROMPTS}
        assert names == {
            "csegraph-index",
            "csegraph-refresh",
            "csegraph-minimal",
            "csegraph-context",
        }

    def test_prompts_have_metadata(self):
        for prompt in _PROMPTS:
            assert prompt.name
            assert prompt.title
            assert prompt.description
            assert "-" in prompt.name

    def test_prompt_arguments_are_declared_for_contextual_workflows(self):
        by_name = {prompt.name: prompt for prompt in _PROMPTS}

        context_args = {arg.name: arg for arg in by_name["csegraph-context"].arguments}
        assert context_args["repo"].required is True
        assert context_args["task"].required is True
        assert context_args["target"].required is False


class TestHandlePrompt:
    def test_context_prompt_references_context_tool(self):
        result = _handle_prompt(
            "csegraph-context",
            {
                "repo": "/repo",
                "task": "fix auth refresh",
                "target": "refresh_token",
            },
        )

        assert result.messages
        message = result.messages[0]
        assert message.role == "user"
        assert message.content.type == "text"
        assert "csegraph_context" in message.content.text
        assert "detail_level=auto" in message.content.text
        assert "/repo" in message.content.text
        assert "fix auth refresh" in message.content.text
        assert "refresh_token" in message.content.text
        assert "Token-efficiency" in message.content.text

    def test_unknown_prompt_raises(self):
        with pytest.raises(ValueError, match="Unknown prompt"):
            _handle_prompt("csegraph-nope", {})


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
        assert ctx["detail_level"] == "auto"
        assert ctx["returned_detail_level"] in {"minimal", "standard"}

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

    def test_graph_minimal_is_default_and_strips_edges(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = str(tmp_path / "test.db")
        _handle_tool("csegraph_index", {"repo": str(repo), "db": db})

        result = _handle_tool("csegraph_graph", {
            "node": "greet",
            "repo": str(repo),
            "db": db,
            "depth": 1,
        })
        assert result["detail_level"] == "minimal"
        assert result["summary"]
        assert result["edges"] == []
        assert result["total_nodes"] >= 1
        assert isinstance(result["truncated"], bool)

    def test_graph_standard_returns_full_data(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = str(tmp_path / "test.db")
        _handle_tool("csegraph_index", {"repo": str(repo), "db": db})

        result = _handle_tool("csegraph_graph", {
            "node": "greet",
            "repo": str(repo),
            "db": db,
            "depth": 1,
            "detail_level": "standard",
        })
        assert result["detail_level"] == "standard"
        assert result["truncated"] is False
        assert result["edges"]

    def test_path_minimal_is_default_with_name_chain(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = str(tmp_path / "test.db")
        _handle_tool("csegraph_index", {"repo": str(repo), "db": db})

        result = _handle_tool("csegraph_path", {
            "source": "greet",
            "target": "fmt",
            "repo": str(repo),
            "db": db,
        })
        assert result["detail_level"] == "minimal"
        assert result["found"] is True
        assert "→" in result["summary"]
        assert result["edges"] == []
        for node in result["nodes"]:
            assert node["path"] == ""
            assert node["line_range"] is None

    def test_graph_tool_declares_detail_level_enum(self):
        graph_tool = next(t for t in _TOOLS if t.name == "csegraph_graph")
        detail = graph_tool.inputSchema["properties"]["detail_level"]
        assert detail["enum"] == ["minimal", "standard"]
        assert detail["default"] == "minimal"

    def test_path_tool_declares_detail_level_enum(self):
        path_tool = next(t for t in _TOOLS if t.name == "csegraph_path")
        detail = path_tool.inputSchema["properties"]["detail_level"]
        assert detail["enum"] == ["minimal", "standard"]
        assert detail["default"] == "minimal"

    def test_path_found(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = str(tmp_path / "test.db")
        _handle_tool("csegraph_index", {"repo": str(repo), "db": db})

        result = _handle_tool("csegraph_path", {
            "source": "greet",
            "target": "fmt",
            "repo": str(repo),
            "db": db,
            "detail_level": "standard",
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
        ]:
            result = _handle_tool(tool_name, args)
            serialized = json.dumps(result)
            assert isinstance(json.loads(serialized), dict)


class TestServerCreation:
    def test_create_server_returns_server(self):
        server = create_server()
        assert server is not None
        assert server.name == "csegraph"

    def test_create_server_with_tool_filter(self):
        server = create_server(allowed_tools=["csegraph_minimal", "csegraph_context"])
        assert server is not None

    def test_create_server_rejects_unknown_tools(self):
        with pytest.raises(ValueError, match="Unknown tool names"):
            create_server(allowed_tools=["csegraph_minimal", "csegraph_fake"])

    def test_core_tool_names_has_6_tools(self):
        assert len(CORE_TOOL_NAMES) == 6
        assert set(CORE_TOOL_NAMES) == {t.name for t in _TOOLS}

    def test_default_server_exposes_core_only(self):
        server = create_server()
        assert asyncio.run(_listed_tool_names(server)) == CORE_TOOL_NAMES

    def test_default_server_exposes_core_prompts_only(self):
        server = create_server()
        assert asyncio.run(_listed_prompt_names(server)) == [
            "csegraph-index",
            "csegraph-refresh",
            "csegraph-minimal",
            "csegraph-context",
        ]

    def test_extended_tools_are_not_registered(self):
        with pytest.raises(ValueError, match="Unknown tool names"):
            create_server(allowed_tools=["csegraph_flows"])


class TestPromptWorkflows:
    def test_context_prompt_enforces_escalation_pattern(self):
        result = _handle_prompt("csegraph-context", {"repo": "/repo", "task": "fix bug"})
        text = result.messages[0].content.text
        assert "Step 1" in text
        assert "csegraph_minimal" in text
        assert "detail_level=auto" in text
        assert "3 tool calls total" in text

    def test_minimal_prompt_respects_suggestions(self):
        result = _handle_prompt("csegraph-minimal", {"repo": "/repo"})
        text = result.messages[0].content.text
        assert "next_tool_suggestions" in text
        assert "stale-index warning" in text

    def test_detect_changes_prompt_is_not_agent_facing(self):
        with pytest.raises(ValueError, match="Unknown prompt"):
            _handle_prompt("csegraph-detect-changes", {"repo": "/repo"})

    def test_all_prompts_include_token_efficiency_preamble(self):
        for prompt in _PROMPTS:
            args = {"repo": "/repo"}
            if any(a.name == "task" and a.required for a in prompt.arguments):
                args["task"] = "test task"
            result = _handle_prompt(prompt.name, args)
            text = result.messages[0].content.text
            assert "Token-efficiency" in text, f"{prompt.name} missing preamble"
