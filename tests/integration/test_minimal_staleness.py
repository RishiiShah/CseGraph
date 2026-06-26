from __future__ import annotations

import time
from pathlib import Path

from csegraph._core.server.app import _handle_tool


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    return repo


def test_minimal_marks_age_only_index_as_cautious_not_stale(tmp_path: Path):
    repo = _make_repo(tmp_path)
    db = str(repo / ".scratch" / "csegraph" / "test.db")

    _handle_tool("csegraph_index", {"repo": str(repo), "db": db})

    # Force nodes.updated_at to >24 hours ago
    old = time.time() - (25 * 3600)
    from csegraph._core.index.repository import ProjectIndex

    idx = ProjectIndex(db)
    try:
        idx.conn.execute("UPDATE nodes SET updated_at = ?", (old,))
        idx.conn.execute(
            "UPDATE metadata SET value = ? WHERE key = 'updated_at'",
            (str(old),),
        )
        idx.conn.commit()
    finally:
        idx.close()

    result = _handle_tool("csegraph_minimal", {"repo": str(repo), "db": db})
    assert result["index_health"]["verdict"] == "aged"
    assert "age-check cautious" in result["summary"].lower()
    assert all(
        s.get("tool") != "csegraph_refresh" for s in result.get("next_tool_suggestions", [])
    )
