from __future__ import annotations

SCHEMA_VERSION = "csegraph-sqlite-v5"
SCHEMA_USER_VERSION = 5

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
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS retrieval_context (
    run_id INTEGER NOT NULL,
    node_id TEXT NOT NULL,
    rank INTEGER NOT NULL,
    score REAL NOT NULL,
    raw_code INTEGER NOT NULL,
    evidence TEXT NOT NULL,
    PRIMARY KEY(run_id, node_id)
);

CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent_id);
CREATE INDEX IF NOT EXISTS idx_nodes_type_name ON nodes(type, name);
CREATE INDEX IF NOT EXISTS idx_nodes_path ON nodes(path);
CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
CREATE INDEX IF NOT EXISTS idx_nodes_is_test ON nodes(is_test) WHERE is_test = 1;
CREATE INDEX IF NOT EXISTS idx_edges_source_relation ON edges(source, relation);
CREATE INDEX IF NOT EXISTS idx_edges_target_relation ON edges(target, relation);
CREATE INDEX IF NOT EXISTS idx_edges_relation ON edges(relation);
"""

METADATA_UPSERT = """
INSERT INTO metadata(key, value)
VALUES('schema_version', ?)
ON CONFLICT(key) DO UPDATE SET value = excluded.value
"""
