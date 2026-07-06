"""Extraction cache — avoids re-parsing files whose SHA256 hasn't changed.

Stores serialized ParsedFile objects in a SQLite table keyed by (rel_path, sha256).
Used by RefreshService to skip AST parsing for unchanged files.
"""

from __future__ import annotations

import json
import sqlite3
import zlib
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from csegraph._core.index.schema import SCHEMA_VERSION
from csegraph._core.languages.types import (
    ParsedFile,
    ParsedImport,
    ParsedReference,
    ParsedSymbol,
)

CACHE_VERSION = f"{SCHEMA_VERSION}:parser-v3"

_CACHE_DDL = """
CREATE TABLE IF NOT EXISTS parse_cache (
    rel_path TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    version TEXT NOT NULL,
    parsed_payload BLOB NOT NULL
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
        self._ensure_current_schema()
        self.conn.execute("DELETE FROM parse_cache WHERE version != ?", (CACHE_VERSION,))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def get(self, rel_path: str, sha256: str) -> Optional[ParsedFile]:
        row = self.conn.execute(
            """
            SELECT parsed_payload FROM parse_cache
            WHERE rel_path = ? AND sha256 = ? AND version = ?
            """,
            (rel_path, sha256, CACHE_VERSION),
        ).fetchone()
        if row is None:
            self.misses += 1
            return None
        self.hits += 1
        return _deserialize(row["parsed_payload"])

    def put(self, parsed: ParsedFile) -> None:
        blob = _serialize(parsed)
        self.conn.execute(
            """
            INSERT INTO parse_cache (rel_path, sha256, version, parsed_payload)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(rel_path) DO UPDATE SET
                sha256 = excluded.sha256,
                version = excluded.version,
                parsed_payload = excluded.parsed_payload
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

    def _ensure_current_schema(self) -> None:
        columns = {row["name"]: row for row in self.conn.execute("PRAGMA table_info(parse_cache)")}
        if columns and (
            "parsed_payload" not in columns
            or "rel_path" not in columns
            or int(columns["rel_path"]["pk"]) != 1
        ):
            self.conn.execute("DROP TABLE parse_cache")
        self.conn.executescript(_CACHE_DDL)


def _serialize(parsed: ParsedFile) -> bytes:
    d = asdict(parsed)
    return zlib.compress(json.dumps(d, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _deserialize(blob: bytes) -> ParsedFile:
    d = json.loads(zlib.decompress(blob).decode("utf-8"))
    symbols = []
    for symbol_data in d.pop("symbols", []):
        references = [ParsedReference(**record) for record in symbol_data.pop("references", [])]
        symbol = ParsedSymbol(**symbol_data)
        symbol.references = references
        symbols.append(symbol)
    import_records = [ParsedImport(**record) for record in d.pop("import_records", [])]
    pf = ParsedFile(**d)
    pf.import_records = import_records
    pf.symbols = symbols
    return pf
