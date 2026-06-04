from __future__ import annotations

import subprocess
import sys

import pytest

from csegraph import ContextService, IndexService
from csegraph._core.core.models import ContextNode
from csegraph._core.core.serializer import to_dict


def _write_repo(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "utils.py").write_text(
        "def helper(x: int) -> int:\n    return x + 1\n",
        encoding="utf-8",
    )
    (root / "main.py").write_text(
        "from utils import helper\n\ndef run(x: int) -> int:\n    return helper(x)\n",
        encoding="utf-8",
    )


def _scratch_path(repo, name):
    return repo / ".scratch" / "csegraph" / name


def test_canonical_nodes_have_language_field(tmp_path):
    repo = tmp_path / "repo"
    db_path = _scratch_path(repo, "index.db")
    _write_repo(repo)
    IndexService(db_path).index(repo, profile="small")

    context = ContextService(db_path).build_context(
        task="Implement run using helper",
        target="run",
        profile="small",
    )

    assert context.nodes
    for node in context.nodes:
        assert node.language == "python"

    payload = to_dict(context)
    assert payload["nodes"]
    for node in payload["nodes"]:
        assert "language" in node
        assert node["language"] == "python"


def test_markdown_output_uses_language_fence(tmp_path):
    repo = tmp_path / "repo"
    db_path = _scratch_path(repo, "index.db")
    _write_repo(repo)
    IndexService(db_path).index(repo, profile="small")

    proc = subprocess.run(
        [
            sys.executable, "-m", "csegraph._cli",
            "context", "Implement run",
            "--target", "run",
            "--db", str(db_path),
            "--repo", str(repo),
            "--format", "markdown",
            "--detail-level", "standard",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "```python" in proc.stdout


def test_context_node_without_language_raises():
    with pytest.raises(TypeError):
        ContextNode(
            id="symbol::main.py::function::run",
            kind="function",
            name="run",
            path="main.py",
            line_range=[3, 4],
            score=1.0,
            # language is intentionally omitted
        )


def test_context_node_with_none_language_raises():
    with pytest.raises(ValueError, match="non-empty"):
        ContextNode(
            id="symbol::main.py::function::run",
            kind="function",
            name="run",
            path="main.py",
            line_range=[3, 4],
            score=1.0,
            language=None,
        )


def test_context_node_with_empty_language_raises():
    with pytest.raises(ValueError, match="non-empty"):
        ContextNode(
            id="symbol::main.py::function::run",
            kind="function",
            name="run",
            path="main.py",
            line_range=[3, 4],
            score=1.0,
            language="",
        )


def test_schema_v5_language_column_notnull(tmp_path):
    import sqlite3
    from csegraph._core.index.repository import ProjectIndex
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    db_path = _scratch_path(repo, "test.db")
    idx = ProjectIndex(db_path)
    idx.initialize_schema()
    idx.set_metadata(str(repo), "small")
    idx.close()
    with sqlite3.connect(db_path) as conn:
        col_info = {row[1]: row for row in conn.execute("PRAGMA table_info(nodes)")}
    assert "language" in col_info
    assert col_info["language"][3] == 1  # notnull flag


def test_real_index_produces_no_null_language(tmp_path):
    import sqlite3
    from csegraph import IndexService
    repo = tmp_path / "repo"
    db_path = _scratch_path(repo, "index.db")
    _write_repo(repo)
    IndexService(db_path).index(repo, profile="small")
    with sqlite3.connect(db_path) as conn:
        null_count = conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE language IS NULL"
        ).fetchone()[0]
    assert null_count == 0


def test_no_empty_language_in_fresh_index(tmp_path):
    """Every nodes row in a fresh index must have a non-empty language."""
    import sqlite3
    from csegraph import IndexService
    repo = tmp_path / "repo"
    db_path = _scratch_path(repo, "index.db")
    _write_repo(repo)
    IndexService(db_path).index(repo, profile="small")
    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE language IS NULL OR language = ''"
        ).fetchone()[0]
    assert count == 0


def test_writer_guard_fires_before_file_insert(tmp_path):
    """IndexService must raise before writing any nodes when language is empty."""
    import sqlite3
    from csegraph._core.index.repository import ProjectIndex
    from csegraph._core.index.services import _write_parsed_files
    from csegraph._core.languages.types import ParsedFile

    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    db_path = _scratch_path(repo, "guard.db")
    idx = ProjectIndex(db_path)
    idx.initialize_schema()
    idx.set_metadata(str(repo), "small")

    bad = ParsedFile(
        rel_path="bad.py",
        abs_path=str(repo / "bad.py"),
        sha256="abc",
        mtime=0.0,
        size=0,
        language="",
    )
    with pytest.raises(ValueError, match="language is required"):
        _write_parsed_files(idx, str(repo), [bad])

    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM nodes WHERE type = 'file'").fetchone()[0]
    idx.close()

    assert count == 0, "no file nodes must be inserted when language guard fires"
