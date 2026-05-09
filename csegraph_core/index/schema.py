from __future__ import annotations

SCHEMA_VERSION = "csegraph-sqlite-v4"
SCHEMA_USER_VERSION = 4

SCHEMA_DDL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    root_dir TEXT NOT NULL UNIQUE,
    active_profile TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL,
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
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    source_node_id TEXT NOT NULL,
    target_node_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    metadata TEXT,
    UNIQUE(project_id, source_node_id, target_node_id, relation, metadata)
);

CREATE TABLE IF NOT EXISTS summaries (
    node_id TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL,
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
    project_id INTEGER NOT NULL,
    model TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    vector BLOB NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS retrieval_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    query_text TEXT NOT NULL,
    target_node_id TEXT,
    profile TEXT NOT NULL,
    dependency_completeness REAL NOT NULL,
    entity_coverage REAL NOT NULL,
    semantic_overlap REAL NOT NULL,
    model_confidence REAL NOT NULL,
    is_sufficient INTEGER NOT NULL,
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

CREATE INDEX IF NOT EXISTS idx_nodes_project_type_name ON nodes(project_id, type, name);
CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent_id);
CREATE INDEX IF NOT EXISTS idx_nodes_path ON nodes(project_id, path);
CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(project_id, name);
CREATE INDEX IF NOT EXISTS idx_nodes_is_test ON nodes(project_id, is_test) WHERE is_test = 1;
CREATE INDEX IF NOT EXISTS idx_edges_source_relation ON edges(source_node_id, relation);
CREATE INDEX IF NOT EXISTS idx_edges_target_relation ON edges(target_node_id, relation);
CREATE INDEX IF NOT EXISTS idx_edges_project_relation ON edges(project_id, relation);
"""

SCHEMA_META_UPSERT = """
INSERT INTO schema_meta(key, value)
VALUES('schema_version', ?)
ON CONFLICT(key) DO UPDATE SET value = excluded.value
"""
