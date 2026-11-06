from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from mcp.types import CallToolRequest, CallToolRequestParams

from csegraph._core.retrieval.token_budget import count_payload_tokens
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


def test_mcp_refresh_returns_changed_source_in_nested_v5_context(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "app.py"
    source.write_text("def run():\n    return 1\n")
    _handle_tool("csegraph_index", {"repo": str(repo)})
    source.write_text("def run():\n    return 2\n")
    handler = create_server().request_handlers[CallToolRequest]

    result = asyncio.run(
        handler(
            CallToolRequest(
                params=CallToolRequestParams(
                    name="csegraph_refresh",
                    arguments={"repo": str(repo), "task": "Inspect run", "token_budget": 256},
                )
            )
        )
    )

    assert not result.root.isError
    payload = json.loads(result.root.content[0].text)
    assert payload["impacted_files"] == ["app.py"]
    context = payload["context"]
    assert set(context) == {"schema_version", "status", "slices"}
    assert context["schema_version"] == "csegraph-context-v5"
    assert context["slices"] == [
        {
            "path": "app.py",
            "lines": [1, 2],
            "symbol": "run",
            "role": "changed",
            "code": "def run():\n    return 2\n",
        }
    ]
    assert count_payload_tokens(context, "o200k_base") <= 256


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
