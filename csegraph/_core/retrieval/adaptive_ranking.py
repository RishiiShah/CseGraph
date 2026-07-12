from __future__ import annotations

from typing import Any, Sequence

from csegraph._core.text.query_tokenizer import query_tokenizer

from .adaptive_caps import HARD_MAX_CANDIDATES


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
        "fts_rank": int(row.get("_fts_rank", HARD_MAX_CANDIDATES)),
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
        int(row.get("_fts_rank", HARD_MAX_CANDIDATES)) if precedence == 3 else 0,
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
            int(row.get("_rank_position", HARD_MAX_CANDIDATES)),
            -float(row.get("_score", 0.0)),
            str(row["path"]),
            int(row.get("start_line") or 0),
        ),
    )


def _select_context_rows(
    rows: Sequence[dict[str, Any]],
    *,
    target_id: str,
    roles: dict[str, str],
    intent: str,
    task: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Keep the target and the relationship types the request asks about."""

    if limit <= 0:
        return []
    prioritized = _prioritize_rows(rows, target_id, roles, intent)
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    for row in prioritized:
        if str(row.get("id")) == target_id:
            selected.append(row)
            selected_ids.add(target_id)
            break

    tokens = query_tokenizer.tokenize(task)
    role_terms = {
        "test": {"test", "tests", "testing", "coverage", "regression"},
        "caller": {"caller", "callers", "reference", "references", "usage", "used"},
        "dependency": {"dependency", "dependencies", "import", "imports", "flow", "routing"},
    }
    ordered_roles = sorted(
        (
            min(
                (position for position, token in enumerate(tokens) if token in terms),
                default=len(tokens),
            ),
            role,
        )
        for role, terms in role_terms.items()
        if any(token in terms for token in tokens)
    )
    requested_roles: list[str] = [role for _, role in ordered_roles]
    if (
        intent == "debug"
        and "test" in tokens
        and not {
            "tests",
            "coverage",
            "regression",
        }.intersection(tokens)
    ):
        requested_roles = [role for role in requested_roles if role != "test"]
    if not requested_roles and intent != "debug":
        requested_roles = ["dependency", "caller", "test"]

    test_candidates = [
        row for row in prioritized if roles.get(str(row.get("id")), "context") == "test"
    ]
    reserve_test_count = (
        min(2, len(test_candidates)) if intent == "edit" and "test" not in requested_roles else 0
    )
    if reserve_test_count:
        requested_roles.sort(key=lambda role: (0 if role == "dependency" else 1, role))
    selection_limit = max(1, limit - reserve_test_count)
    matched_requested_role = False
    for role in requested_roles:
        candidates = [
            row
            for row in prioritized
            if roles.get(str(row.get("id")), "context") == role
            and str(row.get("id")) not in selected_ids
        ]
        if not candidates:
            continue
        matched_requested_role = True
        role_is_plural = (
            (role == "caller" and "callers" in tokens)
            or (role == "dependency" and "dependencies" in tokens)
            or (role == "test" and "tests" in tokens)
            or (intent == "debug" and role == "test")
        )
        take_count = len(candidates) if role_is_plural else 1
        for candidate in candidates[:take_count]:
            selected.append(candidate)
            selected_ids.add(str(candidate.get("id")))
            if len(selected) >= selection_limit:
                break
        if len(selected) >= selection_limit:
            break

    if reserve_test_count and len(selected) < limit:
        for test_candidate in test_candidates:
            if str(test_candidate.get("id")) in selected_ids:
                continue
            selected.append(test_candidate)
            selected_ids.add(str(test_candidate.get("id")))
            if len(selected) >= limit:
                break

    if not matched_requested_role or intent == "debug":
        fallback_roles = (
            ("caller", "test", "dependency")
            if intent == "debug" and not {"dependency", "dependencies"}.intersection(tokens)
            else ("dependency", "caller", "test")
        )
        for role in fallback_roles:
            candidates = [
                row
                for row in prioritized
                if roles.get(str(row.get("id")), "context") == role
                and str(row.get("id")) not in selected_ids
            ]
            if not candidates:
                continue
            take_count = len(candidates) if intent == "debug" else 1
            for candidate in candidates[:take_count]:
                selected.append(candidate)
                selected_ids.add(str(candidate.get("id")))
                if len(selected) >= limit:
                    break
            if len(selected) >= limit:
                break

    return selected[:limit]
