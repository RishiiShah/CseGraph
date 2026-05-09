from __future__ import annotations

import subprocess
import sys

import pytest

from csegraph import ContextService, IndexService
from csegraph_core.core.models import ContextNode
from csegraph_core.core.serializer import to_dict


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


def test_canonical_nodes_have_language_field(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "index.db"
    _write_repo(repo)
    IndexService(db_path).index(repo, profile="small")

    context = ContextService(db_path).build_context(
        task="Implement run using helper",
        target="run",
        profile="small",
    )

    assert context.context_nodes
    for node in context.context_nodes:
        assert node.language == "python"

    payload = to_dict(context)
    assert payload["nodes"]
    for node in payload["nodes"]:
        assert "language" in node
        assert node["language"] == "python"


def test_markdown_output_uses_language_fence(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "index.db"
    _write_repo(repo)
    IndexService(db_path).index(repo, profile="small")

    proc = subprocess.run(
        [
            sys.executable, "-m", "csegraph_cli",
            "context", "Implement run",
            "--target", "run",
            "--db", str(db_path),
            "--repo", str(repo),
            "--format", "markdown",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "```python" in proc.stdout


def test_context_node_without_language_raises():
    with pytest.raises(TypeError):
        ContextNode(
            node_id="symbol::main.py::function::run",
            kind="function",
            name="run",
            file_path="main.py",
            start_line=3,
            end_line=4,
            score=1.0,
            # language is intentionally omitted
        )


def test_context_node_with_none_language_raises():
    with pytest.raises(ValueError, match="non-empty"):
        ContextNode(
            node_id="symbol::main.py::function::run",
            kind="function",
            name="run",
            file_path="main.py",
            start_line=3,
            end_line=4,
            score=1.0,
            language=None,
        )


def test_context_node_with_empty_language_raises():
    with pytest.raises(ValueError, match="non-empty"):
        ContextNode(
            node_id="symbol::main.py::function::run",
            kind="function",
            name="run",
            file_path="main.py",
            start_line=3,
            end_line=4,
            score=1.0,
            language="",
        )


def test_schema_v4_language_column_notnull(tmp_path):
    import sqlite3
    from csegraph_core.index.repository import ProjectIndex
    db_path = tmp_path / "test.db"
    idx = ProjectIndex(db_path)
    idx.initialize_schema()
    idx.close()
    with sqlite3.connect(db_path) as conn:
        col_info = {row[1]: row for row in conn.execute("PRAGMA table_info(nodes)")}
    assert "language" in col_info
    assert col_info["language"][3] == 1  # notnull flag


def test_real_index_produces_no_null_language(tmp_path):
    import sqlite3
    from csegraph import IndexService
    repo = tmp_path / "repo"
    db_path = tmp_path / "index.db"
    _write_repo(repo)
    IndexService(db_path).index(repo, profile="small")
    with sqlite3.connect(db_path) as conn:
        null_count = conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE language IS NULL"
        ).fetchone()[0]
    assert null_count == 0


def test_v3_to_v4_migration_enforces_language_notnull(tmp_path):
    """After the v3→v4 migration the nodes table must have language NOT NULL."""
    import sqlite3
    from csegraph_core.index.repository import ProjectIndex

    db_path = tmp_path / "v3.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta(key, value) VALUES('schema_version', 'csegraph-sqlite-v3');
            CREATE TABLE projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                root_dir TEXT NOT NULL UNIQUE,
                active_profile TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE nodes (
                id TEXT PRIMARY KEY,
                project_id INTEGER NOT NULL,
                parent_id TEXT,
                type TEXT NOT NULL,
                name TEXT NOT NULL,
                path TEXT NOT NULL,
                language TEXT,
                sha256 TEXT,
                signature TEXT,
                docstring TEXT,
                start_line INTEGER,
                end_line INTEGER,
                source_hash TEXT NOT NULL,
                parse_status TEXT,
                parse_error TEXT,
                metadata TEXT,
                is_test INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL
            );
            INSERT INTO nodes(id, project_id, type, name, path, source_hash, is_test, updated_at)
                VALUES('sym::a.py::function::f', 1, 'function', 'f', 'a.py', 'h1', 0, 0);
            CREATE TABLE edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
                source_node_id TEXT NOT NULL, target_node_id TEXT NOT NULL,
                relation TEXT NOT NULL, metadata TEXT,
                UNIQUE(project_id, source_node_id, target_node_id, relation, metadata)
            );
            CREATE TABLE summaries (
                node_id TEXT PRIMARY KEY, project_id INTEGER NOT NULL,
                source_hash TEXT NOT NULL, summary TEXT NOT NULL,
                kind TEXT NOT NULL, updated_at REAL NOT NULL
            );
            CREATE VIRTUAL TABLE lexical_index USING fts5(
                node_id UNINDEXED, name, path, signature, docstring, summary, source
            );
            CREATE TABLE embedding_cache (
                node_id TEXT PRIMARY KEY, project_id INTEGER NOT NULL,
                model TEXT NOT NULL, source_hash TEXT NOT NULL,
                vector BLOB NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE retrieval_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
                query_text TEXT NOT NULL, target_node_id TEXT, profile TEXT NOT NULL,
                dependency_completeness REAL NOT NULL, entity_coverage REAL NOT NULL,
                semantic_overlap REAL NOT NULL, model_confidence REAL NOT NULL,
                is_sufficient INTEGER NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE retrieval_context (
                run_id INTEGER NOT NULL, node_id TEXT NOT NULL, rank INTEGER NOT NULL,
                score REAL NOT NULL, raw_code INTEGER NOT NULL, evidence TEXT NOT NULL,
                PRIMARY KEY(run_id, node_id)
            );
            """
        )

    idx = ProjectIndex(db_path)
    idx.initialize_schema()
    try:
        col_info = {row[1]: row for row in idx.conn.execute("PRAGMA table_info(nodes)")}
        lang_notnull = col_info["language"][3]
        lang_value = idx.conn.execute(
            "SELECT language FROM nodes WHERE id = 'sym::a.py::function::f'"
        ).fetchone()[0]
    finally:
        idx.close()

    assert lang_notnull == 1, "language column must be NOT NULL after v3→v4 migration"
    assert lang_value == "python", "NULL language must be backfilled to 'python'"


def test_no_empty_language_in_fresh_index(tmp_path):
    """Every nodes row in a fresh index must have a non-empty language."""
    import sqlite3
    from csegraph import IndexService
    repo = tmp_path / "repo"
    db_path = tmp_path / "index.db"
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
    from csegraph_core.index.repository import ProjectIndex
    from csegraph_core.index.services import _write_parsed_files
    from csegraph_core.languages.types import ParsedFile

    db_path = tmp_path / "guard.db"
    idx = ProjectIndex(db_path)
    idx.initialize_schema()
    project_id = idx.upsert_project(str(tmp_path / "repo"), "small")

    bad = ParsedFile(
        rel_path="bad.py",
        abs_path=str(tmp_path / "repo" / "bad.py"),
        sha256="abc",
        mtime=0.0,
        size=0,
        language="",
    )
    with pytest.raises(ValueError, match="language is required"):
        _write_parsed_files(idx, project_id, str(tmp_path / "repo"), [bad])

    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM nodes WHERE type = 'file'").fetchone()[0]
    idx.close()

    assert count == 0, "no file nodes must be inserted when language guard fires"
