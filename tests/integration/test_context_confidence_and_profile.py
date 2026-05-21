from __future__ import annotations

from pathlib import Path
from csegraph_core.server.app import _handle_tool
from csegraph_core.config.profiles import load_profile


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def a():\n    return b()\n", encoding="utf-8")
    (repo / "b.py").write_text("def b():\n    return 1\n", encoding="utf-8")
    return repo


def test_context_includes_confidence_breakdown_and_profile_byte_cap(tmp_path: Path):
    repo = _make_repo(tmp_path)
    db = str(tmp_path / "test.db")

    _handle_tool("csegraph_index", {"repo": str(repo), "db": db})

    result = _handle_tool("csegraph_context", {"repo": str(repo), "task": "call b", "db": db, "profile": "small"})
    assert isinstance(result, dict)
    assert "confidence_breakdown" in result and isinstance(result["confidence_breakdown"], dict)

    cfg = load_profile("small")
    # server should apply profile default byte cap when none provided
    assert "byte_cap" in result and result["byte_cap"] == cfg.max_bytes
