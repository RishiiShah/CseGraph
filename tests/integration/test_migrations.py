import sqlite3

from csegraph_core.index.repository import ProjectIndex


def _schema_version(conn: sqlite3.Connection) -> str:
    return conn.execute(
        "SELECT value FROM schema_meta WHERE key='schema_version'"
    ).fetchone()[0]


def _language_notnull(conn: sqlite3.Connection) -> int:
    cols = {row[1]: row for row in conn.execute("PRAGMA table_info(nodes)")}
    return cols["language"][3]


def test_v1_database_upgrades_through_full_chain_preserving_graph_and_fts(tmp_path):
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
                'def foo()', 'Foo docs', 1, 2, 'h2');
            CREATE TABLE edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
                source_id TEXT NOT NULL, target_id TEXT NOT NULL,
                relation TEXT NOT NULL, metadata TEXT,
                UNIQUE(project_id, source_id, target_id, relation, metadata)
            );
            INSERT INTO edges(project_id, source_id, target_id, relation, metadata)
                VALUES(1, 'file::pkg/util.py', 'symbol::pkg/util.py::function::foo', 'contains', NULL);
            CREATE TABLE summaries (
                node_id TEXT PRIMARY KEY, project_id INTEGER NOT NULL,
                source_hash TEXT NOT NULL, summary TEXT NOT NULL, kind TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
            INSERT INTO summaries VALUES('symbol::pkg/util.py::function::foo', 1, 'h2', 'summary text', 'ast', 0);
            CREATE VIRTUAL TABLE lexical_index USING fts5(node_id UNINDEXED, content);
            INSERT INTO lexical_index(node_id, content) VALUES('symbol::pkg/util.py::function::foo', 'foo util summary');
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
        conn = idx.conn
        assert _schema_version(conn) == "csegraph-sqlite-v4"
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert _language_notnull(conn) == 1
        assert conn.execute("SELECT COUNT(*) FROM nodes WHERE language IS NULL OR language = ''").fetchone()[0] == 0
        assert dict(conn.execute("SELECT type, COUNT(*) FROM nodes GROUP BY type").fetchall()) == {
            "file": 1,
            "folder": 1,
            "function": 1,
            "repo": 1,
        }
        assert conn.execute("SELECT COUNT(*) FROM edges WHERE relation = 'contains'").fetchone()[0] == 1
        assert conn.execute("SELECT summary FROM summaries WHERE node_id = 'symbol::pkg/util.py::function::foo'").fetchone()[0] == "summary text"
        fts_cols = {row[1] for row in conn.execute("PRAGMA table_info(lexical_index)")}
        assert {"node_id", "name", "path", "signature", "docstring", "summary", "source"}.issubset(fts_cols)
    finally:
        idx.close()


def test_v2_database_upgrades_through_v3_and_v4_preserving_is_test_and_notnull(tmp_path):
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
                VALUES('/tmp/v2', 'small', 0, 0);
            CREATE TABLE nodes (
                id TEXT PRIMARY KEY, project_id INTEGER NOT NULL, parent_id TEXT,
                type TEXT NOT NULL, name TEXT NOT NULL, path TEXT NOT NULL,
                language TEXT, sha256 TEXT, signature TEXT, docstring TEXT,
                start_line INTEGER, end_line INTEGER, source_hash TEXT NOT NULL,
                parse_status TEXT, parse_error TEXT, metadata TEXT,
                updated_at REAL NOT NULL
            );
            INSERT INTO nodes(id, project_id, parent_id, type, name, path, language, source_hash, metadata, updated_at)
                VALUES('symbol::pkg/test_util.py::function::test_foo', 1, NULL, 'function', 'test_foo', 'pkg/test_util.py', NULL,
                       'h1', '{"is_test": true}', 0);
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
        conn = idx.conn
        assert _schema_version(conn) == "csegraph-sqlite-v4"
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert _language_notnull(conn) == 1
        assert conn.execute("SELECT language FROM nodes WHERE id = 'symbol::pkg/test_util.py::function::test_foo'").fetchone()[0] == "python"
        assert conn.execute("SELECT is_test FROM nodes WHERE id = 'symbol::pkg/test_util.py::function::test_foo'").fetchone()[0] == 1
        fts_cols = {row[1] for row in conn.execute("PRAGMA table_info(lexical_index)")}
        assert {"node_id", "name", "path", "signature", "docstring", "summary", "source"}.issubset(fts_cols)
    finally:
        idx.close()
