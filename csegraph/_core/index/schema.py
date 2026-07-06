from __future__ import annotations

SCHEMA_VERSION = "csegraph-sqlite-v11"
SCHEMA_USER_VERSION = 11

SCHEMA_DDL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    language TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    parse_status TEXT NOT NULL,
    parse_error TEXT,
    size INTEGER NOT NULL DEFAULT 0,
    mtime REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS symbols (
    id TEXT PRIMARY KEY,
    file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    parent_id TEXT REFERENCES symbols(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    signature TEXT,
    docstring TEXT,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    source_hash TEXT NOT NULL,
    is_test INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS edges (
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    relation TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    confidence_tier TEXT NOT NULL DEFAULT 'EXTRACTED',
    PRIMARY KEY (source, target, relation)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS imports (
    file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    import_name TEXT NOT NULL,
    resolved_file_id TEXT REFERENCES files(id) ON DELETE SET NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (file_id, import_name, start_line, end_line, source)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS import_bindings (
    file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    import_name TEXT NOT NULL,
    local_name TEXT NOT NULL,
    imported_name TEXT NOT NULL,
    qualified_name TEXT,
    binding_kind TEXT NOT NULL DEFAULT 'named',
    resolved_file_id TEXT REFERENCES files(id) ON DELETE SET NULL,
    resolved_symbol_id TEXT REFERENCES symbols(id) ON DELETE SET NULL,
    resolution_status TEXT NOT NULL DEFAULT 'unresolved',
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (
        file_id, import_name, local_name, imported_name,
        start_line, end_line, source
    )
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS edge_occurrences (
    occurrence_key BLOB PRIMARY KEY,
    source TEXT NOT NULL,
    target TEXT,
    relation TEXT NOT NULL,
    source_file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    enclosing_symbol_id TEXT REFERENCES symbols(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    source_text TEXT NOT NULL,
    resolution_status TEXT NOT NULL DEFAULT 'resolved',
    resolution_strategy TEXT,
    candidate_targets TEXT
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS summaries (
    node_id TEXT PRIMARY KEY,
    source_hash TEXT NOT NULL,
    summary TEXT NOT NULL,
    kind TEXT NOT NULL,
    updated_at REAL NOT NULL
) WITHOUT ROWID;

CREATE VIRTUAL TABLE IF NOT EXISTS lexical_index USING fts5(
    node_id UNINDEXED,
    name,
    path,
    signature,
    docstring,
    summary,
    source
);

CREATE TABLE IF NOT EXISTS refresh_leases (
    repo_root TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    expires_at REAL NOT NULL
) WITHOUT ROWID;

CREATE VIEW IF NOT EXISTS entities AS
SELECT
    f.id AS id,
    NULL AS parent_id,
    'file' AS type,
    'file' AS kind,
    f.name AS name,
    f.path AS path,
    f.path AS file_path,
    f.language AS language,
    f.sha256 AS sha256,
    NULL AS signature,
    NULL AS docstring,
    NULL AS start_line,
    NULL AS end_line,
    f.sha256 AS source_hash,
    f.parse_status AS parse_status,
    f.parse_error AS parse_error,
    0 AS is_test,
    f.updated_at AS updated_at
FROM files AS f
UNION ALL
SELECT
    s.id AS id,
    COALESCE(s.parent_id, s.file_id) AS parent_id,
    s.kind AS type,
    s.kind AS kind,
    s.name AS name,
    f.path AS path,
    f.path AS file_path,
    f.language AS language,
    NULL AS sha256,
    s.signature AS signature,
    s.docstring AS docstring,
    s.start_line AS start_line,
    s.end_line AS end_line,
    s.source_hash AS source_hash,
    NULL AS parse_status,
    NULL AS parse_error,
    s.is_test AS is_test,
    s.updated_at AS updated_at
FROM symbols AS s
JOIN files AS f ON f.id = s.file_id;

CREATE INDEX IF NOT EXISTS idx_files_name ON files(name);
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_id);
CREATE INDEX IF NOT EXISTS idx_symbols_parent ON symbols(parent_id);
CREATE INDEX IF NOT EXISTS idx_symbols_kind_name ON symbols(kind, name);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_is_test
    ON symbols(is_test) WHERE is_test = 1;
CREATE INDEX IF NOT EXISTS idx_edges_target_relation
    ON edges(target, relation);
CREATE INDEX IF NOT EXISTS idx_edges_relation
    ON edges(relation);
CREATE INDEX IF NOT EXISTS idx_imports_resolved_file
    ON imports(resolved_file_id);
CREATE INDEX IF NOT EXISTS idx_import_bindings_file_local
    ON import_bindings(file_id, local_name);
CREATE INDEX IF NOT EXISTS idx_import_bindings_resolved_file
    ON import_bindings(resolved_file_id);
CREATE INDEX IF NOT EXISTS idx_import_bindings_resolved_symbol
    ON import_bindings(resolved_symbol_id);
CREATE INDEX IF NOT EXISTS idx_edge_occurrences_source_relation
    ON edge_occurrences(source, relation);
CREATE INDEX IF NOT EXISTS idx_edge_occurrences_target_relation
    ON edge_occurrences(target, relation);
CREATE INDEX IF NOT EXISTS idx_refresh_leases_expiry
    ON refresh_leases(expires_at);
"""

METADATA_UPSERT = """
INSERT INTO metadata(key, value)
VALUES('schema_version', ?)
ON CONFLICT(key) DO UPDATE SET value = excluded.value
"""
