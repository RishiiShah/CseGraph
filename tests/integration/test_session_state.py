from __future__ import annotations

from pathlib import Path

from csegraph_core.server.app import _handle_tool
from csegraph_core.server.session import _SESSION


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    return repo


def test_inferred_intent_cached_in_session(tmp_path: Path):
    repo = _make_repo(tmp_path)
    db = str(tmp_path / "test.db")
    # Index the repo first then call minimal with a task that matches 'debug'
    _handle_tool("csegraph_index", {"repo": str(repo), "db": db})
    result = _handle_tool("csegraph_minimal", {"repo": str(repo), "db": db, "task": "debug failing test"})
    assert result["task_intent"] == "debug"
    assert _SESSION.inferred_intent == "debug"
