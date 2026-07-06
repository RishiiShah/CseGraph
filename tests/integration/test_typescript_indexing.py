from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from csegraph import ContextRequest, ContextService, IndexService, RefreshService, to_dict


@pytest.mark.parametrize(
    ("filename", "source", "target"),
    [
        ("service.ts", "export function createUser() { return 1; }\n", "createUser"),
        ("service.js", "export function createUser() { return 1; }\n", "createUser"),
    ],
)
def test_javascript_and_typescript_index_and_retrieve(
    tmp_path: Path,
    filename: str,
    source: str,
    target: str,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / filename).write_text(source, encoding="utf-8")
    db = repo / ".csegraph" / "index.db"

    IndexService(db).index(repo)
    payload = to_dict(
        ContextService(db).retrieve(
            ContextRequest(repo=str(repo), task=f"Explain {target}", target=target)
        )
    )

    with sqlite3.connect(db) as conn:
        language = conn.execute(
            "SELECT language FROM files WHERE path = ?",
            (filename,),
        ).fetchone()
    assert language == ("typescript",)
    assert payload["status"] == "ready"
    assert target in payload["slices"][0]["code"]


def test_typescript_cross_file_resolution_and_refresh(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    helper = repo / "helper.ts"
    helper.write_text("export function formatName(v: string) { return v; }\n", encoding="utf-8")
    (repo / "service.ts").write_text(
        "import { formatName } from './helper';\n"
        "export function greet(v: string) { return formatName(v); }\n",
        encoding="utf-8",
    )
    db = repo / ".csegraph" / "index.db"
    IndexService(db).index(repo)

    with sqlite3.connect(db) as conn:
        edge = conn.execute(
            """
            SELECT source, target, relation FROM edges
            WHERE source LIKE '%::greet' AND target LIKE '%::formatName'
            """
        ).fetchone()
    assert edge is not None
    assert edge[2] == "calls"

    helper.write_text(
        "export function formatName(v: string) { return v.toUpperCase(); }\n",
        encoding="utf-8",
    )
    result = RefreshService(db).refresh(changed_paths=[helper])
    assert result.changed_files == ["helper.ts"]
