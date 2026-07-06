from __future__ import annotations

from typing import Any, Sequence

from .adaptive_constants import (
    MAX_CANDIDATES,
)


def _slice_key(row: dict[str, Any]) -> tuple[str, int, int]:
    return (
        str(row.get("source_hash") or ""),
        int(row.get("start_line") or 0),
        int(row.get("end_line") or 0),
    )


def _deduplicate_ranked_rows(
    rows: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, int]] = set()
    for row in rows:
        source_hash, start_line, end_line = _slice_key(row)
        key = (
            source_hash or str(row.get("id") or ""),
            str(row.get("path") or ""),
            start_line,
            end_line,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _candidate_evidence(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "score": round(float(row.get("_score", 0.0)), 4),
        "precedence": int(row.get("_precedence", 9)),
        "penalty_rank": int(row.get("_penalty_rank", 0)),
        "fts_rank": int(row.get("_fts_rank", MAX_CANDIDATES)),
        "scope_rank": int(row.get("_scope_rank", 2)),
        "graph_rank": int(row.get("_graph_rank", 1)),
        "reasons": list(row.get("_rank_reasons") or []),
    }


def _evidence_int(evidence: dict[str, Any], key: str, default: int) -> int:
    value = evidence.get(key)
    return default if value is None else int(value)


def _candidate_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """Keep public ranking precedence explicit and deterministic.

    Test/generated penalties apply before discovery rank unless the symbol was
    explicitly requested. FTS positions are only meaningful inside their own
    discovery tier.
    """

    precedence = int(row.get("_precedence", 9))
    return (
        precedence,
        int(row.get("_penalty_rank", 0)),
        int(row.get("_fts_rank", MAX_CANDIDATES)) if precedence == 3 else 0,
        int(row.get("_scope_rank", 2)),
        int(row.get("_graph_rank", 1)),
        -float(row.get("_score", 0.0)),
        0 if str(row.get("id") or "").startswith("symbol::") else 1,
        str(row.get("path") or "").casefold(),
        int(row.get("start_line") or 0),
        str(row.get("id") or ""),
    )


def _target_confidence(
    ranked: Sequence[dict[str, Any]],
    *,
    exact: bool,
) -> tuple[float, float]:
    if not ranked:
        return 0.0, 0.0
    if exact:
        return 1.0, 1.0
    top = max(0.0, float(ranked[0].get("_score", 0.0)))
    second = max(0.0, float(ranked[1].get("_score", 0.0))) if len(ranked) > 1 else 0.0
    if top <= 0:
        return 0.0, 0.0
    confidence = top / max(top + second, 1.0)
    margin = (top - second) / max(top, 1.0)
    return round(min(1.0, confidence), 4), round(max(0.0, margin), 4)


def _role_rank(role: str, intent: str) -> int:
    if role == "target":
        return 0
    if intent == "debug":
        order = {"caller": 1, "test": 2, "dependency": 3, "context": 4}
    elif intent == "edit":
        order = {"dependency": 1, "test": 2, "caller": 3, "context": 4}
    else:
        order = {"dependency": 1, "caller": 2, "test": 3, "context": 4}
    return order.get(role, 5)


def _prioritize_rows(
    rows: Sequence[dict[str, Any]],
    target_id: str,
    roles: dict[str, str],
    intent: str,
) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            0 if str(row["id"]) == target_id else 1,
            _role_rank(roles.get(str(row["id"]), "context"), intent),
            int(row.get("_rank_position", MAX_CANDIDATES)),
            -float(row.get("_score", 0.0)),
            str(row["path"]),
            int(row.get("start_line") or 0),
        ),
    )
