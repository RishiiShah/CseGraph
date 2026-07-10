from __future__ import annotations

import sqlite3
from pathlib import Path

from csegraph import IndexService


def test_v12_derives_symbol_language_and_path_from_files(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    db = repo / ".csegraph" / "index.db"
    IndexService(db).index(repo)

    with sqlite3.connect(db) as conn:
        symbol_columns = {row[1] for row in conn.execute("PRAGMA table_info(symbols)")}
        entity = conn.execute(
            "SELECT path, language FROM entities WHERE id = ?",
            ("symbol::app.py::function::run",),
        ).fetchone()

    assert "path" not in symbol_columns
    assert "language" not in symbol_columns
    assert entity == ("app.py", "python")


def test_file_language_is_required(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.ts").write_text("export function run() { return 1; }\n", encoding="utf-8")
    db = repo / ".csegraph" / "index.db"
    IndexService(db).index(repo)

    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT path, language FROM files").fetchall()

    assert rows == [("app.ts", "typescript")]
