CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT INTO metadata(key, value) VALUES
    ('schema_version', 'csegraph-sqlite-v7'),
    ('root_dir', '/fixture/repo'),
    ('active_profile', 'small');

CREATE TABLE nodes (
    id TEXT PRIMARY KEY,
    parent_id TEXT,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    language TEXT NOT NULL,
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
    community_id INTEGER,
    updated_at REAL NOT NULL
);

CREATE TABLE files (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    language TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    parse_status TEXT,
    parse_error TEXT,
    size INTEGER NOT NULL DEFAULT 0,
    mtime REAL NOT NULL DEFAULT 0,
    metadata TEXT,
    updated_at REAL NOT NULL
);

CREATE TABLE symbols (
    id TEXT PRIMARY KEY,
    file_id TEXT NOT NULL,
    parent_id TEXT,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    language TEXT NOT NULL,
    signature TEXT,
    docstring TEXT,
    start_line INTEGER,
    end_line INTEGER,
    source_hash TEXT NOT NULL,
    metadata TEXT,
    is_test INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
);

CREATE TABLE edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    relation TEXT NOT NULL,
    metadata TEXT,
    confidence REAL NOT NULL DEFAULT 1.0,
    confidence_tier TEXT NOT NULL DEFAULT 'EXTRACTED',
    UNIQUE(source, target, relation, metadata)
);

CREATE TABLE retrieval_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,
    target TEXT,
    profile TEXT NOT NULL,
    dependency_completeness REAL NOT NULL,
    entity_coverage REAL NOT NULL,
    semantic_overlap REAL NOT NULL,
    model_confidence REAL NOT NULL,
    sufficient INTEGER NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE retrieval_context (
    run_id INTEGER NOT NULL,
    node_id TEXT NOT NULL,
    rank INTEGER NOT NULL,
    score REAL NOT NULL,
    raw_code INTEGER NOT NULL,
    evidence TEXT NOT NULL,
    PRIMARY KEY(run_id, node_id)
);

INSERT INTO nodes(
    id, parent_id, type, name, path, language, sha256, signature,
    docstring, start_line, end_line, source_hash, parse_status,
    parse_error, metadata, is_test, community_id, updated_at
) VALUES
    ('file::app.py', NULL, 'file', 'app.py', 'app.py', 'python', 'file-hash',
     NULL, NULL, NULL, NULL, 'file-hash', 'ok', NULL, '{}', 0, NULL, 1.0),
    ('symbol::app.py::function::greet', 'file::app.py', 'function', 'greet',
     'app.py', 'python', NULL, 'greet(name)', NULL, 1, 2, 'symbol-hash',
     NULL, NULL, '{}', 0, NULL, 1.0);

INSERT INTO files(
    id, path, name, language, sha256, source_hash, parse_status,
    parse_error, size, mtime, metadata, updated_at
) VALUES(
    'file::app.py', 'app.py', 'app.py', 'python', 'file-hash',
    'file-hash', 'ok', NULL, 32, 1.0, '{}', 1.0
);

INSERT INTO symbols(
    id, file_id, parent_id, kind, name, path, language, signature,
    docstring, start_line, end_line, source_hash, metadata, is_test, updated_at
) VALUES(
    'symbol::app.py::function::greet', 'file::app.py', NULL, 'function',
    'greet', 'app.py', 'python', 'greet(name)', NULL, 1, 2,
    'symbol-hash', '{}', 0, 1.0
);

INSERT INTO retrieval_runs(
    query, target, profile, dependency_completeness, entity_coverage,
    semantic_overlap, model_confidence, sufficient, created_at
) VALUES(
    'Explain greet', 'greet', 'small', 1.0, 1.0, 1.0, 1.0, 1, 1.0
);

INSERT INTO retrieval_context(run_id, node_id, rank, score, raw_code, evidence)
VALUES(1, 'symbol::app.py::function::greet', 0, 1.0, 1, '{}');
