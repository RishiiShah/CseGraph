from __future__ import annotations

from pathlib import Path

import pytest

from csegraph._core.server.app import _handle_tool
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
