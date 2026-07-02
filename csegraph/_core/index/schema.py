from __future__ import annotations

SCHEMA_VERSION = "csegraph-sqlite-v9"
SCHEMA_USER_VERSION = 9

SCHEMA_DDL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nodes (
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

CREATE TABLE IF NOT EXISTS files (
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

CREATE TABLE IF NOT EXISTS symbols (
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

CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    relation TEXT NOT NULL,
    metadata TEXT,
    confidence REAL NOT NULL DEFAULT 1.0,
    confidence_tier TEXT NOT NULL DEFAULT 'EXTRACTED',
    UNIQUE(source, target, relation, metadata)
);

CREATE TABLE IF NOT EXISTS relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    kind TEXT NOT NULL,
    metadata TEXT,
    confidence REAL NOT NULL DEFAULT 1.0,
    confidence_tier TEXT NOT NULL DEFAULT 'EXTRACTED',
    UNIQUE(source, target, kind, metadata)
);

CREATE TABLE IF NOT EXISTS symbol_references (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file_id TEXT NOT NULL,
    enclosing_symbol_id TEXT,
    target TEXT,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    start_line INTEGER,
    end_line INTEGER,
    source TEXT,
    metadata TEXT
);

CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id TEXT NOT NULL,
    path TEXT NOT NULL,
    language TEXT NOT NULL,
    import_name TEXT NOT NULL,
    resolved_file_id TEXT,
    start_line INTEGER,
    end_line INTEGER,
    source TEXT NOT NULL,
    metadata TEXT,
    UNIQUE(file_id, import_name, start_line, end_line, source)
);

CREATE TABLE IF NOT EXISTS import_bindings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id TEXT NOT NULL,
    import_name TEXT NOT NULL,
    local_name TEXT NOT NULL,
    imported_name TEXT NOT NULL,
    qualified_name TEXT,
    binding_kind TEXT NOT NULL DEFAULT 'named',
    resolved_file_id TEXT,
    resolved_symbol_id TEXT,
    resolution_status TEXT NOT NULL DEFAULT 'unresolved',
    start_line INTEGER,
    end_line INTEGER,
    source TEXT NOT NULL,
    metadata TEXT,
    UNIQUE(
        file_id, import_name, local_name, imported_name,
        start_line, end_line, source
    )
);

CREATE TABLE IF NOT EXISTS edge_occurrences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    target TEXT,
    relation TEXT NOT NULL,
    source_file_id TEXT NOT NULL,
    enclosing_symbol_id TEXT,
    name TEXT NOT NULL,
    start_line INTEGER,
    end_line INTEGER,
    source_text TEXT,
    resolution_status TEXT NOT NULL DEFAULT 'resolved',
    resolution_strategy TEXT,
    candidate_targets TEXT,
    is_stale INTEGER NOT NULL DEFAULT 0,
    metadata TEXT,
    UNIQUE(
        source, target, relation, source_file_id, enclosing_symbol_id,
        name, start_line, end_line, source_text, resolution_status, is_stale
    )
);

CREATE TABLE IF NOT EXISTS test_assertions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_symbol_id TEXT NOT NULL,
    source_file_id TEXT NOT NULL,
    target_symbol_id TEXT,
    assertion_kind TEXT NOT NULL,
    expression TEXT NOT NULL,
    start_line INTEGER,
    end_line INTEGER,
    resolution_status TEXT NOT NULL DEFAULT 'unresolved',
    candidate_targets TEXT,
    metadata TEXT,
    UNIQUE(
        test_symbol_id, target_symbol_id, assertion_kind,
        expression, start_line, end_line
    )
);

CREATE TABLE IF NOT EXISTS symbol_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol_id TEXT NOT NULL,
    file_id TEXT NOT NULL,
    path TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    signature TEXT,
    source_hash TEXT NOT NULL,
    start_line INTEGER,
    end_line INTEGER,
    state TEXT NOT NULL DEFAULT 'active',
    replaced_by TEXT,
    metadata TEXT,
    recorded_at REAL NOT NULL,
    UNIQUE(symbol_id, source_hash)
);

CREATE TABLE IF NOT EXISTS summaries (
    node_id TEXT PRIMARY KEY,
    source_hash TEXT NOT NULL,
    summary TEXT NOT NULL,
    kind TEXT NOT NULL,
    updated_at REAL NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS lexical_index USING fts5(
    node_id UNINDEXED,
    name,
    path,
    signature,
    docstring,
    summary,
    source
);

CREATE TABLE IF NOT EXISTS embedding_cache (
    node_id TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    vector BLOB NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS retrieval_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,
    target TEXT,
    profile TEXT NOT NULL,
    dependency_completeness REAL NOT NULL,
    entity_coverage REAL NOT NULL,
    semantic_overlap REAL NOT NULL,
    model_confidence REAL NOT NULL,
    sufficient INTEGER NOT NULL,
    engine TEXT NOT NULL DEFAULT 'legacy',
    index_revision INTEGER NOT NULL DEFAULT 0,
    response_tokens INTEGER NOT NULL DEFAULT 0,
    cursor TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS retrieval_context (
    run_id INTEGER NOT NULL,
    node_id TEXT NOT NULL,
    rank INTEGER NOT NULL,
    score REAL NOT NULL,
    raw_code INTEGER NOT NULL,
    evidence TEXT NOT NULL,
    source_hash TEXT NOT NULL DEFAULT '',
    start_line INTEGER,
    end_line INTEGER,
    PRIMARY KEY(run_id, node_id)
);

CREATE TABLE IF NOT EXISTS retrieval_plan_cache (
    cache_key TEXT PRIMARY KEY,
    index_revision INTEGER NOT NULL,
    plan_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_used_at REAL NOT NULL,
    hit_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS refresh_leases (
    repo_root TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    expires_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent_id);
CREATE INDEX IF NOT EXISTS idx_nodes_type_name ON nodes(type, name);
CREATE INDEX IF NOT EXISTS idx_nodes_path ON nodes(path);
CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
CREATE INDEX IF NOT EXISTS idx_nodes_is_test ON nodes(is_test) WHERE is_test = 1;
CREATE INDEX IF NOT EXISTS idx_files_path ON files(path);
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_id);
CREATE INDEX IF NOT EXISTS idx_symbols_kind_name ON symbols(kind, name);
CREATE INDEX IF NOT EXISTS idx_symbols_path ON symbols(path);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_edges_source_relation ON edges(source, relation);
CREATE INDEX IF NOT EXISTS idx_edges_target_relation ON edges(target, relation);
CREATE INDEX IF NOT EXISTS idx_edges_relation ON edges(relation);
CREATE INDEX IF NOT EXISTS idx_edges_confidence_tier ON edges(confidence_tier);
CREATE INDEX IF NOT EXISTS idx_relationships_source_kind ON relationships(source, kind);
CREATE INDEX IF NOT EXISTS idx_relationships_target_kind ON relationships(target, kind);
CREATE INDEX IF NOT EXISTS idx_relationships_kind ON relationships(kind);
CREATE INDEX IF NOT EXISTS idx_symbol_references_file ON symbol_references(source_file_id);
CREATE INDEX IF NOT EXISTS idx_symbol_references_symbol ON symbol_references(enclosing_symbol_id);
CREATE INDEX IF NOT EXISTS idx_imports_file ON imports(file_id);
CREATE INDEX IF NOT EXISTS idx_imports_path ON imports(path);
CREATE INDEX IF NOT EXISTS idx_imports_resolved_file ON imports(resolved_file_id);
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
CREATE INDEX IF NOT EXISTS idx_edge_occurrences_file
    ON edge_occurrences(source_file_id);
CREATE INDEX IF NOT EXISTS idx_edge_occurrences_stale
    ON edge_occurrences(is_stale, target) WHERE is_stale = 1;
CREATE INDEX IF NOT EXISTS idx_test_assertions_test
    ON test_assertions(test_symbol_id);
CREATE INDEX IF NOT EXISTS idx_test_assertions_target
    ON test_assertions(target_symbol_id);
CREATE INDEX IF NOT EXISTS idx_symbol_history_symbol
    ON symbol_history(symbol_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_symbol_history_path_state
    ON symbol_history(path, state);
CREATE UNIQUE INDEX IF NOT EXISTS idx_retrieval_runs_cursor
    ON retrieval_runs(cursor) WHERE cursor IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_retrieval_plan_revision_used
    ON retrieval_plan_cache(index_revision, last_used_at);
CREATE INDEX IF NOT EXISTS idx_refresh_leases_expiry
    ON refresh_leases(expires_at);
"""

METADATA_UPSERT = """
INSERT INTO metadata(key, value)
VALUES('schema_version', ?)
ON CONFLICT(key) DO UPDATE SET value = excluded.value
"""
