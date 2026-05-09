import sqlite3
from pathlib import Path

import csegraph
import pytest
from csegraph import (
    ContextService,
    GraphQueryService,
    IndexService,
    ProjectIndex,
    RefreshService,
)
from csegraph_core.core.errors import UnsupportedSchemaError
from csegraph_core.retrieval.constants import VALID_REASONS


def _write_sample_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "utils.py").write_text(
        "\n".join(
            [
                "def format_user(name: str) -> str:",
                '    """Normalize a display name."""',
                "    return name.strip().title()",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (root / "main.py").write_text(
        "\n".join(
            [
                "from utils import format_user",
                "",
                "def build_report(name: str) -> str:",
                '    """Build a simple user report."""',
                "    return f'Report: {format_user(name)}'",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_project_index_schema_is_idempotent(tmp_path):
    db_path = tmp_path / "index.db"

    index = ProjectIndex(db_path)
    index.initialize_schema()
    index.initialize_schema()
    index.close()

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table')"
            )
        }

    assert "schema_meta" in tables
    assert "projects" in tables
    assert "nodes" in tables
    assert "edges" in tables
    assert "summaries" in tables
    assert "lexical_index" in tables
    assert "embedding_cache" in tables
    assert "retrieval_runs" in tables
    assert "retrieval_context" in tables
    assert "files" not in tables
    assert "symbols" not in tables

    with sqlite3.connect(db_path) as conn:
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version[0] == "csegraph-sqlite-v4"
    assert user_version == 4


def test_index_context_graph_and_incremental_refresh(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "repo.csegraph.db"
    _write_sample_repo(repo)

    index_result = IndexService(db_path).index(repo, profile="small")

    assert index_result.files_indexed == 2
    assert index_result.symbols_indexed == 2
    assert index_result.edges_indexed >= 3
    assert index_result.profile == "small"

    context = ContextService(db_path).build_context(
        task="Implement build_report using format_user",
        target="symbol::main.py::function::build_report",
        profile="small",
    )

    context_ids = {node.node_id for node in context.context_nodes}
    assert context.target_node_id == "symbol::main.py::function::build_report"
    assert "symbol::main.py::function::build_report" in context_ids
    assert "symbol::utils.py::function::format_user" in context_ids
    assert context.metrics.dependency_completeness == 1.0
    assert context.metrics.entity_coverage == 1.0
    assert context.is_sufficient is True

    graph = GraphQueryService(db_path).neighborhood(
        "symbol::main.py::function::build_report",
        depth=1,
    )
    edge_shapes = {(edge.source, edge.relation, edge.target) for edge in graph.edges}
    assert (
        "symbol::main.py::function::build_report",
        "calls",
        "symbol::utils.py::function::format_user",
    ) in edge_shapes

    no_change = RefreshService(db_path).refresh(profile="small")
    assert no_change.changed_files == []
    assert no_change.deleted_files == []
    assert no_change.files_indexed == 0

    (repo / "utils.py").write_text(
        "\n".join(
            [
                "def format_user(name: str) -> str:",
                '    """Normalize a display name."""',
                "    return name.strip().title()",
                "",
                "def format_title(title: str) -> str:",
                "    return title.strip().upper()",
                "",
            ]
        ),
        encoding="utf-8",
    )

    refreshed = RefreshService(db_path).refresh(profile="small")
    assert refreshed.changed_files == ["utils.py"]
    assert refreshed.files_indexed == 1
    assert "symbol::utils.py::function::format_title" in refreshed.changed_symbols

    refreshed_context = ContextService(db_path).build_context(
        task="Use format_title in reporting",
        target="format_title",
        profile="small",
    )
    assert refreshed_context.target_node_id == "symbol::utils.py::function::format_title"


def test_context_auto_includes_source_for_target_and_direct_dependencies(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "repo.csegraph.db"
    _write_sample_repo(repo)
    IndexService(db_path).index(repo, profile="small")

    context = ContextService(db_path).build_context(
        task="Implement build_report using format_user",
        target="build_report",
        profile="small",
    )

    by_id = {node.node_id: node for node in context.context_nodes}
    target = by_id["symbol::main.py::function::build_report"]
    helper = by_id["symbol::utils.py::function::format_user"]

    assert "def build_report(name: str) -> str:" in target.source_text
    assert "return f'Report: {format_user(name)}'" in target.source_text
    assert "def format_user(name: str) -> str:" in helper.source_text
    assert "return name.strip().title()" in helper.source_text
    assert target.estimated_tokens >= 1
    assert helper.estimated_tokens >= 1
    assert context.estimated_tokens >= target.estimated_tokens + helper.estimated_tokens
    assert "target" in target.reason
    assert "direct_call" in helper.reason
    assert set(target.reason).issubset(VALID_REASONS)
    assert set(helper.reason).issubset(VALID_REASONS)
    assert target.explanation is None
    assert helper.explanation is None


def test_context_explain_populates_human_explanations(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "repo.csegraph.db"
    _write_sample_repo(repo)
    IndexService(db_path).index(repo, profile="small")

    context = ContextService(db_path).build_context(
        task="Implement build_report using format_user",
        target="build_report",
        profile="small",
        explain=True,
    )

    by_id = {node.node_id: node for node in context.context_nodes}
    assert by_id["symbol::main.py::function::build_report"].explanation
    assert "target" in by_id["symbol::main.py::function::build_report"].reason
    helper = by_id["symbol::utils.py::function::format_user"]
    assert "direct_call" in helper.reason
    assert "directly called by the target" in helper.explanation


def test_context_include_source_never_stays_compact(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "repo.csegraph.db"
    _write_sample_repo(repo)
    IndexService(db_path).index(repo, profile="small")

    context = ContextService(db_path).build_context(
        task="Implement build_report using format_user",
        target="build_report",
        profile="small",
        include_source="never",
    )

    assert context.context_nodes
    assert all(node.source_text is None for node in context.context_nodes)
    assert all(node.estimated_tokens >= 1 for node in context.context_nodes)
    assert context.estimated_tokens == sum(node.estimated_tokens for node in context.context_nodes)


def test_context_max_tokens_limits_source_materialization(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "repo.csegraph.db"
    _write_sample_repo(repo)
    IndexService(db_path).index(repo, profile="small")

    context = ContextService(db_path).build_context(
        task="Implement build_report using format_user",
        target="build_report",
        profile="small",
        include_source="always",
        max_tokens=30,
    )

    by_id = {node.node_id: node for node in context.context_nodes}
    assert context.estimated_tokens <= 30
    assert "symbol::main.py::function::build_report" in by_id
    helper = by_id.get("symbol::utils.py::function::format_user")
    assert helper is None or helper.source_text is None


def test_context_reason_enum_is_strict(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "repo.csegraph.db"
    _write_sample_repo(repo)
    IndexService(db_path).index(repo, profile="small")

    context = ContextService(db_path).build_context(
        task="Implement build_report using format_user",
        target="build_report",
        profile="small",
    )

    for node in context.context_nodes:
        assert node.reason
        assert set(node.reason).issubset(VALID_REASONS)
        assert all("expanded-from-" not in reason for reason in node.reason)


def test_v12_emits_inherits_decorates_and_tested_by(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "base.py").write_text(
        "class Animal:\n    def speak(self): return ''\n\ndef cached(fn):\n    return fn\n",
        encoding="utf-8",
    )
    (repo / "dog.py").write_text(
        "from base import Animal, cached\n\nclass Dog(Animal):\n    @cached\n    def speak(self): return 'woof'\n",
        encoding="utf-8",
    )
    (repo / "tests").mkdir()
    (repo / "tests" / "test_dog.py").write_text(
        "from dog import Dog\n\ndef test_dog_speak():\n    assert Dog().speak() == 'woof'\n",
        encoding="utf-8",
    )

    db_path = tmp_path / "repo.db"
    IndexService(db_path).index(repo, profile="small")
    with sqlite3.connect(db_path) as conn:
        relations = {row[0] for row in conn.execute("SELECT DISTINCT relation FROM edges")}
        types = {row[0] for row in conn.execute("SELECT DISTINCT type FROM nodes")}
    assert {"inherits", "decorates", "tested_by"}.issubset(relations)
    assert {"repo", "folder", "file", "class", "function", "method"}.issubset(types)


def test_v12_migrates_v1_database_in_place(tmp_path):
    db_path = tmp_path / "v1.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta(key, value) VALUES('schema_version', 'csegraph-sqlite-v1');
            CREATE TABLE projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT, root_dir TEXT NOT NULL UNIQUE,
                active_profile TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            INSERT INTO projects(root_dir, active_profile, created_at, updated_at)
                VALUES('/tmp/legacy', 'small', 0, 0);
            CREATE TABLE files (
                id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
                path TEXT NOT NULL, language TEXT NOT NULL, sha256 TEXT NOT NULL,
                mtime REAL NOT NULL, size INTEGER NOT NULL,
                parse_status TEXT NOT NULL, parse_error TEXT, updated_at REAL NOT NULL,
                UNIQUE(project_id, path)
            );
            INSERT INTO files(project_id, path, language, sha256, mtime, size, parse_status, parse_error, updated_at)
                VALUES(1, 'pkg/util.py', 'python', 'h1', 0, 1, 'ok', NULL, 0);
            CREATE TABLE symbols (
                id TEXT PRIMARY KEY, project_id INTEGER NOT NULL, file_id INTEGER NOT NULL,
                kind TEXT NOT NULL, name TEXT NOT NULL, parent_symbol_id TEXT,
                signature TEXT, docstring TEXT, start_line INTEGER, end_line INTEGER,
                source_hash TEXT NOT NULL
            );
            INSERT INTO symbols VALUES('symbol::pkg/util.py::function::foo', 1, 1, 'function', 'foo', NULL,
                'def foo()', '', 1, 2, 'h2');
            CREATE TABLE edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
                source_id TEXT NOT NULL, target_id TEXT NOT NULL,
                relation TEXT NOT NULL, metadata TEXT,
                UNIQUE(project_id, source_id, target_id, relation, metadata)
            );
            INSERT INTO edges(project_id, source_id, target_id, relation, metadata)
                VALUES(1, 'file::pkg/util.py', 'symbol::pkg/util.py::function::foo', 'contains', NULL);
            """
        )

    idx = ProjectIndex(db_path)
    idx.initialize_schema()
    try:
        version = idx.conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
        user_version = idx.conn.execute("PRAGMA user_version").fetchone()[0]
        types = dict(idx.conn.execute("SELECT type, COUNT(*) FROM nodes GROUP BY type").fetchall())
        edge_cols = {row[1] for row in idx.conn.execute("PRAGMA table_info(edges)")}
        legacy_tables = [
            row[0] for row in idx.conn.execute(
                "SELECT name FROM sqlite_master WHERE name IN ('files','symbols')"
            )
        ]
    finally:
        idx.close()
    assert version == "csegraph-sqlite-v4"
    assert user_version == 4
    assert types == {"repo": 1, "folder": 1, "file": 1, "function": 1}
    assert {"source_node_id", "target_node_id"}.issubset(edge_cols)
    assert legacy_tables == []


def test_v121_migrates_v2_to_v3_in_place(tmp_path):
    """Build a v2-shaped DB by hand, open with v3 code, verify the v2→v3 migration."""
    db_path = tmp_path / "v2.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta(key, value) VALUES('schema_version', 'csegraph-sqlite-v2');
            CREATE TABLE projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT, root_dir TEXT NOT NULL UNIQUE,
                active_profile TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            INSERT INTO projects(root_dir, active_profile, created_at, updated_at)
                VALUES('/tmp/v2_legacy', 'small', 0, 0);
            CREATE TABLE nodes (
                id TEXT PRIMARY KEY, project_id INTEGER NOT NULL, parent_id TEXT,
                type TEXT NOT NULL, name TEXT NOT NULL, path TEXT NOT NULL,
                language TEXT, sha256 TEXT, signature TEXT, docstring TEXT,
                start_line INTEGER, end_line INTEGER, source_hash TEXT NOT NULL,
                parse_status TEXT, parse_error TEXT, metadata TEXT,
                updated_at REAL NOT NULL
            );
            -- one regular function and one test function (encoded via metadata.is_test in v2)
            INSERT INTO nodes(id, project_id, parent_id, type, name, path,
                              source_hash, metadata, updated_at)
                VALUES('symbol::pkg/util.py::function::foo', 1, NULL, 'function', 'foo', 'pkg/util.py',
                       'h1', '{"is_test": false}', 0);
            INSERT INTO nodes(id, project_id, parent_id, type, name, path,
                              source_hash, metadata, updated_at)
                VALUES('symbol::tests/test_foo.py::function::test_foo', 1, NULL, 'function', 'test_foo',
                       'tests/test_foo.py', 'h2', '{"is_test": true}', 0);
            CREATE TABLE edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
                source_node_id TEXT NOT NULL, target_node_id TEXT NOT NULL,
                relation TEXT NOT NULL, metadata TEXT,
                UNIQUE(project_id, source_node_id, target_node_id, relation, metadata)
            );
            CREATE TABLE summaries (
                node_id TEXT PRIMARY KEY, project_id INTEGER NOT NULL,
                source_hash TEXT NOT NULL, summary TEXT NOT NULL, kind TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE VIRTUAL TABLE lexical_index USING fts5(node_id UNINDEXED, content);
            CREATE TABLE embedding_cache (
                node_id TEXT PRIMARY KEY, project_id INTEGER NOT NULL,
                model TEXT NOT NULL, source_hash TEXT NOT NULL, vector BLOB NOT NULL,
                updated_at REAL NOT NULL
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
        version = idx.conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
        user_version = idx.conn.execute("PRAGMA user_version").fetchone()[0]
        node_cols = {row[1] for row in idx.conn.execute("PRAGMA table_info(nodes)")}
        is_test_count = idx.conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE is_test = 1"
        ).fetchone()[0]
        fts_cols = [row[1] for row in idx.conn.execute("PRAGMA table_info(lexical_index)")]
    finally:
        idx.close()

    assert version == "csegraph-sqlite-v4"
    assert user_version == 4
    assert "is_test" in node_cols
    assert is_test_count == 1  # backfill from metadata JSON
    assert {"name", "path", "signature", "docstring", "summary", "source"}.issubset(set(fts_cols))


def test_unsupported_schema_version_raises_structured_error(tmp_path):
    db_path = tmp_path / "future.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta(key, value) VALUES('schema_version', 'csegraph-sqlite-v999');
            """
        )

    idx = ProjectIndex(db_path)
    try:
        with pytest.raises(UnsupportedSchemaError) as exc_info:
            idx.initialize_schema()
    finally:
        idx.close()

    assert exc_info.value.error_code == "unsupported_schema"
    assert exc_info.value.hint == "Rebuild the index or install a compatible csegraph-core version."


def test_malformed_schema_metadata_raises_structured_error(tmp_path):
    db_path = tmp_path / "malformed.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE nodes (id TEXT PRIMARY KEY);
            """
        )

    idx = ProjectIndex(db_path)
    try:
        with pytest.raises(UnsupportedSchemaError) as exc_info:
            idx.initialize_schema()
    finally:
        idx.close()

    assert exc_info.value.error_code == "unsupported_schema"


def test_sdk_facade_does_not_export_codegen():
    assert hasattr(csegraph, "ContextService")
    assert not hasattr(csegraph, "CodegenService")
    assert not hasattr(csegraph, "CodegenResult")
