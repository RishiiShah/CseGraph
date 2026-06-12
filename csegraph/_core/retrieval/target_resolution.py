"""Target resolution for context retrieval (exact match, ambiguity, inference)."""
from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from csegraph._core.index.repository import ProjectIndex
from csegraph._core.retrieval.scoring import lexical_scores

_SYMBOL_TYPES = ("class", "function", "method", "test")
_MAX_CANDIDATES = 8


@dataclass
class TargetResolution:
    status: str  # resolved | ambiguous | inferred | unresolved
    target_id: str
    requested: Optional[str] = None
    candidates: List[Dict[str, Any]] = field(default_factory=list)


def resolve_target(
    target: Optional[str],
    task: str,
    symbols: Dict[str, Dict[str, Any]],
    summaries: Dict[str, str],
    index: Optional[ProjectIndex] = None,
    repo_root: str = "",
) -> TargetResolution:
    if not target:
        scores, _ = lexical_scores(task, symbols, summaries, fts_seed=None)
        if not scores:
            return TargetResolution(status="unresolved", target_id="", requested=None)
        best_id = max(scores.items(), key=lambda item: item[1])[0]
        return TargetResolution(status="inferred", target_id=best_id, requested=None)

    requested = target.strip()
    if requested in symbols:
        return TargetResolution(
            status="resolved",
            target_id=requested,
            requested=requested,
        )

    if index is None:
        return TargetResolution(status="unresolved", target_id="", requested=requested)

    if not repo_root:
        try:
            repo_root = index.metadata().get("root_dir", "")
        except Exception:
            repo_root = ""

    lowered = requested.lower()

    if repo_root:
        file_id = _resolve_file_path(requested, repo_root, index)
        if file_id:
            return TargetResolution(
                status="resolved",
                target_id=file_id,
                requested=requested,
            )

    exact = _query_symbol_candidates(
        index,
        "SELECT id, type, name, path FROM nodes WHERE type IN ({types})"
        " AND LOWER(name) = ? ORDER BY length(name) ASC, path ASC LIMIT ?",
        (lowered, _MAX_CANDIDATES + 1),
    )
    resolution = _resolution_from_candidates(exact, requested, match="exact_name")
    if resolution is not None:
        return resolution

    path_matches = _query_symbol_candidates(
        index,
        "SELECT id, type, name, path FROM nodes WHERE type IN ({types})"
        " AND LOWER(path) = ? ORDER BY path ASC LIMIT ?",
        (lowered, _MAX_CANDIDATES + 1),
    )
    resolution = _resolution_from_candidates(path_matches, requested, match="exact_path")
    if resolution is not None:
        return resolution

    fuzzy = _query_symbol_candidates(
        index,
        "SELECT id, type, name, path FROM nodes WHERE type IN ({types})"
        " AND (LOWER(name) LIKE ? OR LOWER(path) LIKE ?)"
        " ORDER BY length(name) ASC, path ASC LIMIT ?",
        (f"%{lowered}%", f"%{lowered}%", _MAX_CANDIDATES + 1),
    )
    resolution = _resolution_from_candidates(fuzzy, requested, match="fuzzy")
    if resolution is not None:
        return resolution

    return TargetResolution(status="unresolved", target_id="", requested=requested)


def _resolution_from_candidates(
    rows: List[Dict[str, Any]],
    requested: str,
    *,
    match: str,
) -> Optional[TargetResolution]:
    if not rows:
        return None
    candidates = [_candidate_payload(row, match=match) for row in rows[:_MAX_CANDIDATES]]
    if len(rows) == 1:
        return TargetResolution(
            status="resolved",
            target_id=rows[0]["id"],
            requested=requested,
        )
    return TargetResolution(
        status="ambiguous",
        target_id="",
        requested=requested,
        candidates=candidates,
    )


def _query_symbol_candidates(
    index: ProjectIndex,
    sql_template: str,
    params: tuple[Any, ...],
) -> List[Dict[str, Any]]:
    placeholders = ",".join("?" for _ in _SYMBOL_TYPES)
    sql = sql_template.format(types=placeholders)
    return [
        dict(row)
        for row in index.conn.execute(sql, (*_SYMBOL_TYPES, *params))
    ]


def _candidate_payload(row: Dict[str, Any], *, match: str) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "path": row["path"],
        "kind": row["type"],
        "match": match,
    }


def _resolve_file_path(target: str, repo_root: str, index: ProjectIndex) -> str | None:
    try:
        norm_path = _normalized_repo_relative_path(target)
        if norm_path:
            row = index.conn.execute(
                "SELECT id FROM nodes WHERE type = 'file' AND LOWER(path) = ? LIMIT 1",
                (norm_path,),
            ).fetchone()
            if row is not None:
                return row["id"]

        abs_target_path = Path(target).resolve()
        resolved_root = Path(repo_root).resolve()
        if not abs_target_path.is_relative_to(resolved_root):
            return None
        rel_path = abs_target_path.relative_to(resolved_root).as_posix()
        row = index.conn.execute(
            "SELECT id FROM nodes WHERE type = 'file' AND LOWER(path) = ? LIMIT 1",
            (rel_path.lower(),),
        ).fetchone()
        return row["id"] if row is not None else None
    except Exception:
        return None


def _normalized_repo_relative_path(target: str) -> str | None:
    normalized = target.replace("\\", "/").strip()
    if not normalized or normalized.startswith("/"):
        return None
    normalized = posixpath.normpath(normalized)
    if normalized in {"", ".", ".."} or normalized.startswith("../"):
        return None
    return normalized.lower()
