from __future__ import annotations

from pathlib import Path

from csegraph._core.server.app import _handle_tool


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def a():\n    return b()\n", encoding="utf-8")
    (repo / "b.py").write_text("def b():\n    return 1\n", encoding="utf-8")
    return repo


def _scratch_db(repo: Path) -> str:
    return str(repo / ".scratch" / "csegraph" / "test.db")


def test_context_includes_confidence_breakdown(tmp_path: Path):
    repo = _make_repo(tmp_path)
    db = _scratch_db(repo)

    _handle_tool("csegraph_index", {"repo": str(repo), "db": db})

    result = _handle_tool("csegraph_context", {"repo": str(repo), "task": "call b", "db": db})
    assert isinstance(result, dict)
    assert "confidence_breakdown" in result and isinstance(result["confidence_breakdown"], dict)


def test_no_implicit_byte_cap_without_max_bytes(tmp_path: Path):
    """Without explicit max_bytes, no byte cap is applied (opt-in only)."""
    repo = _make_repo(tmp_path)
    db = _scratch_db(repo)

    _handle_tool("csegraph_index", {"repo": str(repo), "db": db})

    result = _handle_tool("csegraph_context", {"repo": str(repo), "task": "call b", "db": db})
    assert result["byte_cap_applied"] is False
    assert "byte_cap" not in result or result.get("byte_cap") is None


def test_explicit_max_bytes_is_honored(tmp_path: Path):
    repo = _make_repo(tmp_path)
    db = _scratch_db(repo)

    _handle_tool("csegraph_index", {"repo": str(repo), "db": db})

    result = _handle_tool(
        "csegraph_context",
        {"repo": str(repo), "task": "call b", "db": db, "max_bytes": 800},
    )
    assert result["byte_cap"] == 800
    assert result["byte_cap_applied"] is True
    assert result["response_bytes"] == len(__import__("json").dumps(result, default=str).encode())
