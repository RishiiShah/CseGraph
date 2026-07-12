"""Refresh planning queries and dependency impact calculations."""

from __future__ import annotations

import json
from typing import Dict, Iterable, List, Optional, Sequence

from csegraph._core.index.repository import ProjectIndex


def _existing_indexed_paths(
    index: ProjectIndex,
    candidates: Iterable[str],
) -> List[str]:
    unique = sorted(set(candidates))
    existing: List[str] = []
    for offset in range(0, len(unique), 400):
        batch = unique[offset : offset + 400]
        placeholders = ",".join("?" for _ in batch)
        existing.extend(
            str(row["path"])
            for row in index.conn.execute(
                f"SELECT path FROM files WHERE path IN ({placeholders})",
                tuple(batch),
            )
        )
    return sorted(existing)


def _indexed_untracked_from_metadata(metadata: Dict[str, str]) -> Optional[set[str]]:
    raw = metadata.get("indexed_untracked_paths")
    if raw is None:
        return None
    try:
        decoded = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return set()
    if not isinstance(decoded, list):
        return set()
    return {str(path) for path in decoded if isinstance(path, str)}


def _stored_file_hashes(
    index: ProjectIndex,
    candidates: Iterable[str],
) -> Dict[str, str]:
    unique = sorted(set(candidates))
    stored: Dict[str, str] = {}
    for offset in range(0, len(unique), 400):
        batch = unique[offset : offset + 400]
        placeholders = ",".join("?" for _ in batch)
        stored.update(
            {
                str(row["path"]): str(row["sha256"])
                for row in index.conn.execute(
                    f"SELECT path, sha256 FROM files WHERE path IN ({placeholders})",
                    tuple(batch),
                )
            }
        )
    return stored


def _read_refresh_impact(
    index: ProjectIndex,
    rel_paths: Sequence[str],
) -> List[Dict[str, object]]:
    if not rel_paths:
        return []
    placeholders = ",".join("?" for _ in rel_paths)
    snapshots = [
        dict(row)
        for row in index.conn.execute(
            f"""
            SELECT
                s.id AS symbol_id
            FROM symbols AS s
            JOIN files AS f ON f.id = s.file_id
            WHERE f.path IN ({placeholders})
            """,
            tuple(rel_paths),
        )
    ]
    return snapshots


def _find_dependent_files(
    index: ProjectIndex,
    changed_node_ids: List[str],
    already_processed: set[str],
    limit: int,
) -> tuple[List[str], bool]:
    """Find files containing nodes that directly depend on changed nodes.

    Returns (dep_file_paths, cap_hit) where cap_hit is True if the limit was reached.
    """
    if not changed_node_ids or limit <= 0:
        return [], False

    changed_values = ",".join("(?)" for _ in changed_node_ids)
    processed = sorted(already_processed)
    processed_filter = ""
    if processed:
        processed_filter = f"AND path NOT IN ({','.join('?' for _ in processed)})"
    rows = index.conn.execute(
        f"""
        WITH changed(id) AS (
            VALUES {changed_values}
        ),
        dependent_ids(id) AS (
            SELECT e.source
            FROM changed AS c
            JOIN edges AS e ON e.target = c.id
            WHERE e.relation IN ('calls', 'imports', 'inherits')
            UNION
            SELECT e.target
            FROM changed AS c
            JOIN edges AS e ON e.source = c.id
            WHERE e.relation IN ('tested_by', 'decorates')
        ),
        resolved_paths(path) AS (
            SELECT COALESCE(
                (
                    SELECT f.path
                    FROM files AS f
                    WHERE f.id = d.id
                ),
                (
                    SELECT f.path
                    FROM symbols AS s
                    JOIN files AS f ON f.id = s.file_id
                    WHERE s.id = d.id
                )
            )
            FROM dependent_ids AS d
        )
        SELECT DISTINCT path
        FROM resolved_paths
        WHERE path IS NOT NULL
        {processed_filter}
        ORDER BY path
        LIMIT ?
        """,
        (*changed_node_ids, *processed, limit + 1),
    ).fetchall()

    dep_paths = [str(row["path"]) for row in rows]
    cap_hit = len(rows) > limit
    return dep_paths[:limit], cap_hit
