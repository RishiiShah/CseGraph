"""Regression tests for the 15 multi-agent review findings."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest
from mcp.types import CallToolRequest

from csegraph._core.daemon import _log_file, _validate_alias
from csegraph._core.graph.queries import GraphQueryService
from csegraph._core.graph.resolvers import _probe_ts_file
from csegraph._core.index.services import IndexService
from csegraph._core.registry import RegistryService
from csegraph._core.server.app import _handle_tool, create_server


def _indexed(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    db = str(repo / ".scratch" / "csegraph" / "test.db")
    IndexService(db).index(repo, profile="small")
    return repo, db


class TestAliasValidation:
    def test_registry_rejects_path_traversal_alias(self, tmp_path: Path):
        repo = tmp_path / "app"
        repo.mkdir()
        svc = RegistryService(tmp_path / "registry.json")
        with pytest.raises(ValueError, match="Invalid alias"):
            svc.register(repo, alias="../../evil")

    def test_daemon_log_path_rejects_bad_alias(self, tmp_path: Path):
        with pytest.raises(ValueError, match="Invalid alias"):
            _log_file("../evil", base_dir=tmp_path)

    def test_validate_alias_rejects_empty_and_dots(self):
        with pytest.raises(ValueError, match="Invalid alias"):
            _validate_alias("")
        with pytest.raises(ValueError, match="Invalid alias"):
            _validate_alias("bad/alias")


class TestMcpProtocol:
    def test_call_tool_returns_is_error_on_handler_failure(self, tmp_path: Path):
        async def _run():
            repo = tmp_path / "repo"
            repo.mkdir()
            server = create_server(allowed_tools=["csegraph_minimal"])
            handler = server.request_handlers[CallToolRequest]
            req = CallToolRequest(
                method="tools/call",
                params={
                    "name": "csegraph_context",
                    "arguments": {"repo": str(repo), "task": "t"},
                },
            )
            return await handler(req)

        result = asyncio.run(_run())
        payload = result.root
        assert payload.isError is True
        assert "not enabled" in payload.content[0].text

    def test_max_bytes_string_rejected_by_handle_tool(self, tmp_path: Path):
        repo, db = _indexed(tmp_path)
        with pytest.raises(TypeError, match="max_bytes must be an integer"):
            _handle_tool(
                "csegraph_context",
                {"repo": str(repo), "task": "t", "db": db, "max_bytes": "512"},
            )

    def test_max_bytes_whole_float_coerced(self, tmp_path: Path):
        repo, db = _indexed(tmp_path)
        result = _handle_tool(
            "csegraph_context",
            {
                "repo": str(repo),
                "task": "t",
                "db": db,
                "target": "foo",
                "max_bytes": 1024.0,
                "response_mode": "legacy-v3",
            },
        )
        assert result["byte_cap"] == 1024


class TestGraphQueries:
    def test_invalid_confidence_tiers_raise(self, tmp_path: Path):
        _, db = _indexed(tmp_path)
        with pytest.raises(ValueError, match="Unknown confidence_tiers"):
            GraphQueryService(db).neighborhood(
                "foo",
                detail_level="standard",
                confidence_tiers=["INVALID"],
            )

    def test_shortest_path_empty_relation_strings_unfiltered(self, tmp_path: Path):
        _, db = _indexed(tmp_path)
        full = GraphQueryService(db).neighborhood("foo", depth=2, detail_level="standard")
        filtered = GraphQueryService(db).neighborhood(
            "foo",
            depth=2,
            detail_level="standard",
            relations=[""],
        )
        assert len(filtered.edges) == len(full.edges)
        assert filtered.relations_filter == []


class TestResolversPathNormalization:
    def test_probe_ts_file_preserves_dot_hidden_prefix(self):
        node_id = "file::.hidden/bar.ts"
        files = {".hidden/bar.ts": node_id}
        assert _probe_ts_file("./.hidden/bar.ts", "/repo", files) == node_id
        assert _probe_ts_file(".hidden/bar.ts", "/repo", files) == node_id


class TestCliServe:
    def test_serve_rejects_empty_tools_list(self):
        proc = subprocess.run(
            [sys.executable, "-m", "csegraph._cli", "serve", "--tools", ""],
            capture_output=True,
            text=True,
        )
        assert proc.returncode != 0
        assert "empty list" in (proc.stderr + proc.stdout).lower()
