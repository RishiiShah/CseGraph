"""Extraction cache — avoids re-parsing files whose SHA256 hasn't changed.

Stores serialized ParsedFile objects in a SQLite table keyed by (rel_path, sha256).
Used by RefreshService to skip AST parsing for unchanged files.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from csegraph._core.index.schema import SCHEMA_VERSION
from csegraph._core.languages.types import ParsedFile, ParsedSymbol


CACHE_VERSION = f"{SCHEMA_VERSION}:parser-v2"

_CACHE_DDL = """
CREATE TABLE IF NOT EXISTS parse_cache (
    rel_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    version TEXT NOT NULL,
    parsed_json TEXT NOT NULL,
    PRIMARY KEY (rel_path, sha256)
);
"""


class ExtractionCache:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))
        self.hits = 0
        self.misses = 0
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_CACHE_DDL)
        self._ensure_version_column()

    def close(self) -> None:
        self.conn.close()

    def get(self, rel_path: str, sha256: str) -> Optional[ParsedFile]:
        row = self.conn.execute(
            """
            SELECT parsed_json FROM parse_cache
            WHERE rel_path = ? AND sha256 = ? AND version = ?
            """,
            (rel_path, sha256, CACHE_VERSION),
        ).fetchone()
        if row is None:
            self.misses += 1
            return None
        self.hits += 1
        return _deserialize(row["parsed_json"])

    def put(self, parsed: ParsedFile) -> None:
        blob = _serialize(parsed)
        self.conn.execute(
            """
            INSERT INTO parse_cache (rel_path, sha256, version, parsed_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(rel_path, sha256) DO UPDATE SET
                version = excluded.version,
                parsed_json = excluded.parsed_json
            """,
            (parsed.rel_path, parsed.sha256, CACHE_VERSION, blob),
        )
        self.conn.commit()

    def clear(self) -> None:
        self.conn.execute("DELETE FROM parse_cache")
        self.conn.commit()

    def stats(self) -> dict:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM parse_cache").fetchone()
        return {"cached_files": row["c"], "hits": self.hits, "misses": self.misses}

    def _ensure_version_column(self) -> None:
        columns = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(parse_cache)")
        }
        if "version" not in columns:
            self.conn.execute(
                "ALTER TABLE parse_cache ADD COLUMN version TEXT NOT NULL DEFAULT ''"
            )
            self.conn.commit()


def _serialize(parsed: ParsedFile) -> str:
    d = asdict(parsed)
    return json.dumps(d, sort_keys=True)


def _deserialize(blob: str) -> ParsedFile:
    d = json.loads(blob)
    symbols = [ParsedSymbol(**s) for s in d.pop("symbols", [])]
    pf = ParsedFile(**d)
    pf.symbols = symbols
    return pf
