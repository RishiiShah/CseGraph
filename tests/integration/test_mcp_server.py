from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from mcp.types import CallToolRequest, CallToolRequestParams

from csegraph._core.server.app import _handle_tool, create_server
from csegraph._core.server.tools import CORE_MCP_TOOL_NAMES, TOOLS


def test_mcp_exposes_exactly_six_strict_tools():
    assert (
        {tool.name for tool in TOOLS}
        == CORE_MCP_TOOL_NAMES
        == {
            "csegraph_index",
            "csegraph_refresh",
            "csegraph_minimal",
            "csegraph_context",
            "csegraph_graph",
            "csegraph_path",
        }
    )
    assert all(tool.inputSchema.get("additionalProperties") is False for tool in TOOLS)


def test_direct_dispatch_rejects_unknown_properties(tmp_path: Path):
    with pytest.raises(ValueError, match="Unknown arguments"):
        _handle_tool(
            "csegraph_context",
            {"repo": str(tmp_path), "task": "Explain", "response_mode": "legacy-v3"},
        )


def test_index_and_context_dispatch_use_compact_v5(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")

    indexed = _handle_tool("csegraph_index", {"repo": str(repo)})
    context = _handle_tool(
        "csegraph_context",
        {"repo": str(repo), "task": "Explain run", "target": "run"},
    )

    assert indexed["files_indexed"] == 1
    assert set(context) == {"schema_version", "status", "slices"}
    assert context["schema_version"] == "csegraph-context-v5"


def test_all_public_tool_handlers_dispatch_through_worker_threads(tmp_path: Path, monkeypatch):
    calls: list[str] = []

    async def recording_to_thread(func, name, arguments, **kwargs):
        calls.append(name)
        return {"tool": name}

    monkeypatch.setattr(
        "csegraph._core.server.app.asyncio.to_thread",
        recording_to_thread,
    )
    server = create_server()
    handler = server.request_handlers[CallToolRequest]
    repo = str(tmp_path)
    arguments_by_tool = {
        "csegraph_index": {"repo": repo},
        "csegraph_refresh": {"repo": repo},
        "csegraph_minimal": {"repo": repo},
        "csegraph_context": {"repo": repo, "task": "Explain"},
        "csegraph_graph": {"repo": repo, "node": "app.py"},
        "csegraph_path": {"repo": repo, "source": "a", "target": "b"},
    }

    async def exercise_handlers() -> None:
        for name, arguments in arguments_by_tool.items():
            await handler(
                CallToolRequest(
                    params=CallToolRequestParams(name=name, arguments=arguments),
                )
            )

    asyncio.run(exercise_handlers())

    assert calls == list(arguments_by_tool)
