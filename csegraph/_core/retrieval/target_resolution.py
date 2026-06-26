"""Target resolution for context retrieval (exact match, ambiguity, inference)."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from csegraph._core.index.repository import ProjectIndex
from csegraph._core.retrieval.scoring import lexical_scores

_SYMBOL_TYPES = ("class", "function", "method", "test", "document")
_MAX_CANDIDATES = 8
_DEBUG_TASK_TERMS = {
    "bug",
    "crash",
    "debug",
    "error",
    "exception",
    "failed",
    "failing",
    "failure",
    "fix",
    "regression",
    "traceback",
}


@dataclass
class TargetResolution:
    status: str  # resolved | ambiguous | inferred | unresolved
    target_id: str
    requested: Optional[str] = None
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    confidence: Optional[float] = None
    score_margin: Optional[float] = None


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
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        best_id, best_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
        margin = max(0.0, best_score - second_score)
        margin_ratio = margin / max(best_score, 1.0)
        absolute = min(1.0, max(0.0, (best_score - 0.01) / 12.0))
        confidence = round(max(0.0, min(1.0, 0.70 * margin_ratio + 0.30 * absolute)), 4)
        candidates = [
            _candidate_payload_with_score(symbols[node_id], score=score, match="inferred")
            for node_id, score in ranked[:_MAX_CANDIDATES]
            if node_id in symbols
        ]
        if _requires_debug_target_confirmation(task, confidence, margin):
            return TargetResolution(
                status="ambiguous",
                target_id="",
                requested=None,
                candidates=candidates[:4],
                confidence=confidence,
                score_margin=round(margin, 4),
            )
        return TargetResolution(
            status="inferred",
            target_id=best_id,
            requested=None,
            candidates=candidates,
            confidence=confidence,
            score_margin=round(margin, 4),
        )

    requested = target.strip()
    if requested in symbols:
        return TargetResolution(
            status="resolved",
            target_id=requested,
            requested=requested,
            confidence=1.0,
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
                confidence=1.0,
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
            confidence=1.0,
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
    return [dict(row) for row in index.conn.execute(sql, (*_SYMBOL_TYPES, *params))]


def _candidate_payload(row: Dict[str, Any], *, match: str) -> Dict[str, Any]:
    graph_target_id = row["id"] if _is_graph_node_id(row["id"]) else None
    return {
        "id": row["id"],
        "graph_target_id": graph_target_id,
        "name": row["name"],
        "path": row["path"],
        "kind": row["type"],
        "match": match,
    }


def _candidate_payload_with_score(
    row: Dict[str, Any],
    *,
    score: float,
    match: str,
) -> Dict[str, Any]:
    payload = _candidate_payload(row, match=match)
    payload["score"] = round(float(score), 4)
    return payload


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


def _requires_debug_target_confirmation(task: str, confidence: float, margin: float) -> bool:
    tokens = {token.lower() for token in task.replace("_", " ").split()}
    normalized_tokens = {token.strip(".,:;!?()[]{}'\"") for token in tokens}
    is_debug_task = bool(normalized_tokens & _DEBUG_TASK_TERMS)
    return is_debug_task and (confidence < 0.55 or margin <= 0.01)


def _is_graph_node_id(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(("symbol::", "file::", "folder::"))
