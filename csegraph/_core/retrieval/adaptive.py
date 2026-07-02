from __future__ import annotations

import hashlib
import importlib.util
import json
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Sequence

from csegraph._core.core.models import (
    ContextRequest,
    ContextResponse,
    ContextSlice,
    ContextStatus,
    ContextTarget,
)
from csegraph._core.index.repository import ProjectIndex
from csegraph._core.retrieval.freshness import FreshnessCoordinator, FreshnessResult
from csegraph._core.retrieval.token_budget import (
    SUPPORTED_ENCODINGS,
    count_payload_tokens,
    response_bytes,
    response_tokens,
    validate_token_budget,
)
from csegraph._core.text.query_tokenizer import query_tokenizer
from csegraph._core.text.source_reader import read_source_lines

ADAPTIVE_SCHEMA_VERSION = "csegraph-context-v4"
ADAPTIVE_ENGINE_VERSION = "adaptive-v1"
MAX_CANDIDATES = 64
MAX_SLICES = 5
PLAN_CACHE_LIMIT = 1_000
PLAN_CACHE_TTL_SECONDS = 24 * 60 * 60
TARGET_CONFIDENCE_THRESHOLD = 0.75
TARGET_MARGIN_THRESHOLD = 0.15
EMBEDDING_FALLBACK_THRESHOLD = 0.65

_EDIT_WORDS = {
    "add",
    "change",
    "edit",
    "fix",
    "implement",
    "migrate",
    "modify",
    "refactor",
    "remove",
    "rename",
    "replace",
    "update",
}
_DEBUG_WORDS = {
    "bug",
    "crash",
    "debug",
    "error",
    "exception",
    "failed",
    "failing",
    "failure",
    "regression",
    "traceback",
}
_TEST_WORDS = {"assert", "coverage", "pytest", "test", "tests"}
_STRUCTURAL_WORDS = {
    "architecture",
    "blast",
    "callers",
    "dependency",
    "dependencies",
    "graph",
    "impact",
    "path",
    "structure",
}


class AdaptiveContextService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def retrieve(self, request: ContextRequest) -> ContextResponse:
        started = time.perf_counter()
        _validate_request(request)
        freshness = FreshnessCoordinator(self.db_path).ensure_current(request.repo)
        if freshness.status is not None:
            return _terminal_freshness_response(request, freshness, started)

        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            metadata = index.metadata()
            repo_root = str(Path(metadata["root_dir"]).resolve())
            revision = index.index_revision()
            intent = _infer_intent(request.task, request.task_kind)
            plan_mode = _plan_mode(request.task, intent)
            embedding_available = _embedding_available(index)
            cache_key = _plan_cache_key(
                revision,
                request.task,
                request.target,
                intent,
                embedding_available,
            )
            cached_plan = _load_cached_plan(index, cache_key, revision)
            cache_state = "hit" if cached_plan is not None else "miss"

            if cached_plan is None:
                ranked, exact = _discover_candidates(
                    index,
                    request.task,
                    request.target,
                )
                confidence, margin = _target_confidence(ranked, exact=exact)
                semantic_used = False
                if (
                    confidence < EMBEDDING_FALLBACK_THRESHOLD
                    and embedding_available
                    and len(ranked) < MAX_CANDIDATES
                ):
                    semantic_ids = _semantic_candidate_ids(
                        self.db_path,
                        request.task,
                        MAX_CANDIDATES - len(ranked),
                    )
                    if semantic_ids:
                        ranked = _merge_semantic_candidates(index, ranked, semantic_ids)
                        confidence, margin = _target_confidence(ranked, exact=exact)
                        semantic_used = True

                resolved = bool(
                    ranked
                    and (
                        exact
                        or (
                            confidence >= TARGET_CONFIDENCE_THRESHOLD
                            and margin >= TARGET_MARGIN_THRESHOLD
                        )
                    )
                )
                target_id = str(ranked[0]["id"]) if resolved else ""
                relationships: list[dict[str, Any]] = []
                roles: dict[str, str] = {}
                if target_id:
                    roles[target_id] = "target"
                    if plan_mode == "impact":
                        ranked, roles, relationships = _expand_one_hop(
                            index,
                            ranked,
                            target_id,
                            intent,
                        )
                cached_plan = {
                    "ranked_ids": [str(row["id"]) for row in ranked[:MAX_CANDIDATES]],
                    "roles": roles,
                    "target_id": target_id,
                    "confidence": confidence,
                    "margin": margin,
                    "exact": exact,
                    "resolved": resolved,
                    "plan_mode": plan_mode,
                    "semantic_used": semantic_used,
                    "relationships": relationships,
                }
                _store_cached_plan(index, cache_key, revision, cached_plan)

            rows = _load_candidate_rows(index, cached_plan.get("ranked_ids", []))
            ordered = [
                rows[node_id]
                for node_id in cached_plan.get("ranked_ids", [])
                if node_id in rows
            ]
            target_id = str(cached_plan.get("target_id") or "")
            resolved = bool(cached_plan.get("resolved") and target_id in rows)
            confidence = float(cached_plan.get("confidence") or 0.0)
            margin = float(cached_plan.get("margin") or 0.0)
            roles = {
                str(key): str(value)
                for key, value in (cached_plan.get("roles") or {}).items()
            }
            emitted = _cursor_emitted_slices(index, request.cursor, revision)

            if not resolved:
                response = _ambiguous_response(
                    request=request,
                    intent=intent,
                    candidates=ordered[:3],
                    freshness=freshness,
                    revision=revision,
                    cache_state=cache_state,
                    plan=cached_plan,
                    started=started,
                )
                _record_adaptive_run(index, request, response, revision, [])
                return response

            target_row = rows[target_id]
            target = _context_target(target_row, confidence)
            ordered = _prioritize_rows(ordered, target_id, roles, intent)
            if cached_plan.get("plan_mode") != "impact":
                ordered = [target_row]
            else:
                ordered = [
                    row
                    for row in ordered
                    if str(row["id"]) == target_id or str(row["id"]) in roles
                ]
            cursor = uuid.uuid4().hex
            response = ContextResponse(
                schema_version=ADAPTIVE_SCHEMA_VERSION,
                status=ContextStatus.READY,
                intent=intent,
                target=target,
                slices=[],
                freshness={
                    "state": freshness.state,
                    "revision": revision,
                    "refreshed_files": freshness.refreshed_files,
                },
                usage={
                    "tokens": 0,
                    "budget": request.token_budget,
                    "encoding": request.encoding,
                    "latency_ms": 0.0,
                    "cache": cache_state,
                },
                cursor=cursor,
                warnings=list(freshness.warnings),
            )
            if cached_plan.get("plan_mode") == "structural":
                response.next = {
                    "tool": "csegraph_graph",
                    "arguments": {
                        "node": target_id,
                        "depth": 1,
                        "detail_level": "minimal",
                    },
                    "reason": "The task asks for structural context; inspect the focused neighborhood.",
                }
            if request.response_mode == "diagnostic":
                response.diagnostic = {
                    "plan": cached_plan.get("plan_mode"),
                    "confidence": round(confidence, 4),
                    "score_margin": round(margin, 4),
                    "semantic_fallback": bool(cached_plan.get("semantic_used")),
                    "ranked_candidates": [
                        {
                            "id": row["id"],
                            "name": row["name"],
                            "path": row["path"],
                            "score": round(float(row.get("_score", 0.0)), 4),
                        }
                        for row in ordered[:8]
                    ],
                    "relationships": list(cached_plan.get("relationships") or []),
                }

            selected_rows: list[dict[str, Any]] = []
            target_seen_in_cursor = _slice_key(target_row) in emitted
            target_slice_required = request.include_source != "never" and not target_seen_in_cursor
            for row in ordered:
                if len(response.slices) >= MAX_SLICES:
                    break
                if _slice_key(row) in emitted:
                    continue
                role = roles.get(str(row["id"]), "context")
                code = (
                    ""
                    if request.include_source == "never"
                    else _read_symbol_source(repo_root, row) or ""
                )
                candidate = ContextSlice(
                    id=str(row["id"]),
                    path=str(row["path"]),
                    lines=_line_range(row),
                    symbol=str(row["name"]),
                    role=role,
                    code=code,
                )
                response.slices.append(candidate)
                if _response_within_limits(response, request):
                    selected_rows.append(row)
                    continue
                response.slices.pop()
                if str(row["id"]) == target_id and target_slice_required:
                    required = _required_target_budget(response, candidate, request)
                    response.status = ContextStatus.INSUFFICIENT
                    response.cursor = None
                    response.missing = [
                        {
                            "kind": "target_source",
                            "path": row["path"],
                            "lines": _line_range(row),
                            "required_budget": required,
                        }
                    ]
                    response.next = {
                        "tool": "csegraph_context",
                        "arguments": {
                            "target": target_id,
                            "token_budget": min(required, 16_384),
                        },
                        "reason": "The complete target symbol does not fit the current budget.",
                    }
                    break

            if (
                response.status == ContextStatus.READY
                and target_slice_required
                and not any(item.id == target_id for item in response.slices)
            ):
                response.status = ContextStatus.INSUFFICIENT
                response.missing = [
                    {
                        "kind": "target_source",
                        "path": target_row["path"],
                        "lines": _line_range(target_row),
                    }
                ]

            _finalize_response(response, request, started)
            _record_adaptive_run(index, request, response, revision, selected_rows)
            return response
        finally:
            index.close()


def _validate_request(request: ContextRequest) -> None:
    if not request.task or not request.task.strip():
        raise ValueError("task must be a non-empty string")
    validate_token_budget(request.token_budget)
    if request.encoding not in SUPPORTED_ENCODINGS:
        raise ValueError(f"encoding must be one of: {', '.join(SUPPORTED_ENCODINGS)}")
    if request.include_source not in {"auto", "always", "never"}:
        raise ValueError("include_source must be one of: auto, always, never")
    if request.task_kind not in {"auto", "edit", "understand", "review", "test-impact"}:
        raise ValueError(
            "task_kind must be one of: auto, edit, understand, review, test-impact"
        )
    if request.response_mode not in {"compact", "diagnostic"}:
        raise ValueError("adaptive response_mode must be compact or diagnostic")
    if request.engine != "adaptive":
        raise ValueError("AdaptiveContextService requires engine='adaptive'")
    if request.max_bytes is not None:
        if isinstance(request.max_bytes, bool) or not isinstance(request.max_bytes, int):
            raise TypeError("max_bytes must be an integer")
        if request.max_bytes < 256:
            raise ValueError("max_bytes must be at least 256")


def _terminal_freshness_response(
    request: ContextRequest,
    freshness: FreshnessResult,
    started: float,
) -> ContextResponse:
    status = (
        ContextStatus.INDEX_REQUIRED
        if freshness.status == "index_required"
        else ContextStatus.REFRESH_REQUIRED
    )
    response = ContextResponse(
        schema_version=ADAPTIVE_SCHEMA_VERSION,
        status=status,
        intent=_infer_intent(request.task, request.task_kind),
        target=None,
        slices=[],
        freshness={
            "state": freshness.state,
            "revision": freshness.revision,
            "refreshed_files": freshness.refreshed_files,
        },
        usage={
            "tokens": 0,
            "budget": request.token_budget,
            "encoding": request.encoding,
            "latency_ms": 0.0,
            "cache": "miss",
        },
        next=freshness.next,
        warnings=list(freshness.warnings),
    )
    _finalize_response(response, request, started)
    return response


def _infer_intent(task: str, requested: str) -> str:
    if requested != "auto":
        return "debug" if requested == "test-impact" else requested
    tokens = set(query_tokenizer.tokenize(task))
    if tokens & _DEBUG_WORDS:
        return "debug"
    if tokens & _EDIT_WORDS:
        return "edit"
    if "review" in tokens or "merge" in tokens:
        return "review"
    return "understand"


def _plan_mode(task: str, intent: str) -> str:
    tokens = set(query_tokenizer.tokenize(task))
    if tokens & _STRUCTURAL_WORDS:
        return "structural"
    if intent in {"edit", "debug", "review"}:
        return "impact"
    return "lexical"


def _discover_candidates(
    index: ProjectIndex,
    task: str,
    target: str | None,
) -> tuple[list[dict[str, Any]], bool]:
    candidate_scores: dict[str, float] = {}
    exact_ids: set[str] = set()
    target_exact = False
    target_rows: list[dict[str, Any]] = []
    if target:
        target_rows, exact = _target_candidate_rows(index, target)
        target_exact = exact
        for position, row in enumerate(target_rows):
            candidate_scores[str(row["id"])] = 100.0 - position
            if exact:
                exact_ids.add(str(row["id"]))

    fts_ids = _fts_candidate_ids(index, task, MAX_CANDIDATES)
    for position, node_id in enumerate(fts_ids):
        candidate_scores[node_id] = max(
            candidate_scores.get(node_id, 0.0),
            30.0 - (position / max(1, len(fts_ids))) * 10.0,
        )

    task_tokens = sorted(
        {
            token.lower()
            for token in query_tokenizer.tokenize(task)
            if len(token) > 2
        }
    )
    if task_tokens:
        placeholders = ",".join("?" for _ in task_tokens)
        rows = index.conn.execute(
            f"""
            SELECT id
            FROM symbols
            WHERE LOWER(name) IN ({placeholders})
            LIMIT ?
            """,
            (*task_tokens, MAX_CANDIDATES),
        ).fetchall()
        for row in rows:
            node_id = str(row["id"])
            candidate_scores[node_id] = max(candidate_scores.get(node_id, 0.0), 60.0)
            exact_ids.add(node_id)

    rows_by_id = _load_candidate_rows(index, candidate_scores.keys())
    task_lower = task.lower()
    explicit_target = (target or "").lower()
    for node_id, row in rows_by_id.items():
        score = candidate_scores.get(node_id, 0.0)
        name = str(row["name"]).lower()
        path = str(row["path"]).lower()
        if name and name in task_lower:
            score += 20.0
        if path and path in task_lower:
            score += 15.0
        if explicit_target and (
            name == explicit_target or path == explicit_target or node_id.lower() == explicit_target
        ):
            score += 50.0
            exact_ids.add(node_id)
        overlap = set(query_tokenizer.tokenize(f"{name} {path}")) & set(task_tokens)
        score += float(len(overlap)) * 2.0
        if bool(row.get("is_test")) and not (set(task_tokens) & _TEST_WORDS):
            score *= 0.45
        row["_score"] = score

    ranked = sorted(
        rows_by_id.values(),
        key=lambda row: (
            -float(row.get("_score", 0.0)),
            0 if str(row["id"]).startswith("symbol::") else 1,
            str(row["path"]),
            int(row.get("start_line") or 0),
        ),
    )
    exact = bool(
        ranked
        and (
            (target_exact and str(ranked[0]["id"]) in {str(row["id"]) for row in target_rows})
            or (
                not target
                and str(ranked[0]["id"]) in exact_ids
                and sum(1 for row in ranked if str(row["id"]) in exact_ids) == 1
            )
        )
    )
    return ranked[:MAX_CANDIDATES], exact


def _target_candidate_rows(
    index: ProjectIndex,
    target: str,
) -> tuple[list[dict[str, Any]], bool]:
    requested = target.strip()
    row = index.conn.execute(
        "SELECT id FROM symbols WHERE id = ? LIMIT 1",
        (requested,),
    ).fetchone()
    if row is not None:
        return [{"id": row["id"]}], True

    exact_name = index.conn.execute(
        """
        SELECT id FROM symbols
        WHERE LOWER(name) = LOWER(?)
        ORDER BY path, start_line
        LIMIT 9
        """,
        (requested,),
    ).fetchall()
    if exact_name:
        return [dict(item) for item in exact_name], len(exact_name) == 1

    qualified = index.conn.execute(
        """
        SELECT id FROM symbols
        WHERE LOWER(id) LIKE '%::' || LOWER(?)
           OR LOWER(id) LIKE '%.' || LOWER(?)
        ORDER BY path, start_line
        LIMIT 9
        """,
        (requested, requested),
    ).fetchall()
    if qualified:
        return [dict(item) for item in qualified], len(qualified) == 1

    normalized_path = requested.replace("\\", "/").lstrip("./")
    exact_path = index.conn.execute(
        """
        SELECT id FROM symbols
        WHERE LOWER(path) = LOWER(?)
        ORDER BY start_line
        LIMIT 9
        """,
        (normalized_path,),
    ).fetchall()
    if exact_path:
        return [dict(item) for item in exact_path], len(exact_path) == 1

    fuzzy = index.conn.execute(
        """
        SELECT id FROM symbols
        WHERE LOWER(name) LIKE LOWER(?) OR LOWER(path) LIKE LOWER(?)
        ORDER BY LENGTH(name), path, start_line
        LIMIT 9
        """,
        (f"%{requested}%", f"%{normalized_path}%"),
    ).fetchall()
    return [dict(item) for item in fuzzy], False


def _fts_candidate_ids(index: ProjectIndex, task: str, limit: int) -> list[str]:
    tokens = [
        token
        for token in query_tokenizer.tokenize(task)
        if token and len(token) > 1
    ]
    if not tokens:
        return []
    expression = " OR ".join(f'"{token.replace(chr(34), "")}"' for token in tokens)
    try:
        rows = index.conn.execute(
            """
            SELECT node_id
            FROM lexical_index
            WHERE lexical_index MATCH ?
            ORDER BY bm25(lexical_index, 8.0, 4.0, 2.0, 1.0, 2.0, 1.0)
            LIMIT ?
            """,
            (expression, limit),
        ).fetchall()
    except Exception:
        return []
    return [str(row["node_id"]) for row in rows]


def _load_candidate_rows(
    index: ProjectIndex,
    node_ids: Iterable[str],
) -> dict[str, dict[str, Any]]:
    unique = list(dict.fromkeys(str(node_id) for node_id in node_ids if node_id))
    if not unique:
        return {}
    placeholders = ",".join("?" for _ in unique)
    rows = index.conn.execute(
        f"""
        SELECT
            s.id, s.kind, s.name, s.path, s.language, s.signature,
            s.docstring, s.start_line, s.end_line, s.source_hash, s.is_test,
            COALESCE(sm.summary, '') AS summary
        FROM symbols s
        LEFT JOIN summaries sm ON sm.node_id = s.id
        WHERE s.id IN ({placeholders})
        """,
        tuple(unique),
    ).fetchall()
    return {str(row["id"]): dict(row) for row in rows}


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


def _expand_one_hop(
    index: ProjectIndex,
    ranked: list[dict[str, Any]],
    target_id: str,
    intent: str,
) -> tuple[list[dict[str, Any]], dict[str, str], list[dict[str, Any]]]:
    edges = [
        dict(row)
        for row in index.conn.execute(
            """
            SELECT source, target, relation, confidence, confidence_tier
            FROM edges
            WHERE source = ? OR target = ?
            ORDER BY
                CASE relation
                    WHEN 'calls' THEN 0
                    WHEN 'tested_by' THEN 1
                    WHEN 'inherits' THEN 2
                    WHEN 'imports' THEN 3
                    ELSE 4
                END,
                source,
                target
            LIMIT 48
            """,
            (target_id, target_id),
        )
    ]
    neighbor_ids: list[str] = []
    roles = {target_id: "target"}
    for edge in edges:
        outgoing = edge["source"] == target_id
        other = str(edge["target"] if outgoing else edge["source"])
        neighbor_ids.append(other)
        if edge["relation"] == "tested_by":
            roles[other] = "test"
        elif outgoing:
            roles[other] = "dependency"
        else:
            roles[other] = "caller"

    loaded = _load_candidate_rows(index, neighbor_ids)
    existing = {str(row["id"]): row for row in ranked}
    base_score = float(ranked[0].get("_score", 0.0)) if ranked else 1.0
    for position, node_id in enumerate(neighbor_ids):
        row = loaded.get(node_id)
        if row is None:
            continue
        role = roles.get(node_id, "context")
        bonus = {"test": 8.0, "dependency": 7.0, "caller": 6.0}.get(role, 4.0)
        if intent == "debug" and role == "test":
            bonus += 4.0
        row["_score"] = base_score - 10.0 + bonus - position * 0.01
        existing.setdefault(node_id, row)
    expanded = sorted(
        existing.values(),
        key=lambda row: (
            0 if str(row["id"]) == target_id else 1,
            _role_rank(roles.get(str(row["id"]), "context"), intent),
            -float(row.get("_score", 0.0)),
            str(row["path"]),
        ),
    )
    return expanded[:MAX_CANDIDATES], roles, edges


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
            -float(row.get("_score", 0.0)),
            str(row["path"]),
            int(row.get("start_line") or 0),
        ),
    )


def _context_target(row: dict[str, Any], confidence: float) -> ContextTarget:
    return ContextTarget(
        id=str(row["id"]),
        name=str(row["name"]),
        kind=str(row["kind"]),
        path=str(row["path"]),
        lines=_line_range(row),
        confidence=round(confidence, 4),
    )


def _line_range(row: dict[str, Any]) -> list[int] | None:
    start = row.get("start_line")
    end = row.get("end_line")
    if start is None or end is None:
        return None
    return [int(start), int(end)]


def _read_symbol_source(repo_root: str, row: dict[str, Any]) -> str | None:
    line_range = _line_range(row)
    if line_range is None:
        return None
    return read_source_lines(
        repo_root,
        str(row["path"]),
        line_range[0],
        line_range[1],
    )


def _slice_key(row: dict[str, Any]) -> tuple[str, int, int]:
    return (
        str(row.get("source_hash") or ""),
        int(row.get("start_line") or 0),
        int(row.get("end_line") or 0),
    )


def _response_within_limits(response: ContextResponse, request: ContextRequest) -> bool:
    tokens = response_tokens(response)
    if tokens > request.token_budget:
        return False
    return request.max_bytes is None or response_bytes(response) <= request.max_bytes


def _required_target_budget(
    response: ContextResponse,
    target_slice: ContextSlice,
    request: ContextRequest,
) -> int:
    response.slices.append(target_slice)
    try:
        return count_payload_tokens(
            _response_payload_without_self_count(response),
            request.encoding,
        )
    finally:
        response.slices.pop()


def _response_payload_without_self_count(response: ContextResponse) -> dict[str, Any]:
    from csegraph._core.core.serializer import to_dict

    payload = to_dict(response)
    usage = payload.get("usage")
    if isinstance(usage, dict):
        usage["tokens"] = 0
    return payload


def _finalize_response(
    response: ContextResponse,
    request: ContextRequest,
    started: float,
) -> None:
    response.usage["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
    while True:
        tokens = response_tokens(response)
        bytes_ok = request.max_bytes is None or response_bytes(response) <= request.max_bytes
        if tokens <= request.token_budget and bytes_ok:
            return
        if response.diagnostic is not None:
            response.diagnostic = None
            continue
        if response.warnings:
            response.warnings.pop()
            continue
        if len(response.slices) > 1:
            response.slices.pop()
            continue
        if response.next is not None:
            response.next = None
            continue
        if response.cursor is not None:
            response.cursor = None
            continue
        if response.missing and any(len(item) > 1 for item in response.missing):
            response.missing = [
                {"kind": str(item.get("kind") or "response_budget")}
                for item in response.missing
            ]
            continue
        if "refreshed_files" in response.freshness:
            response.freshness.pop("refreshed_files", None)
            continue
        if response.slices:
            response.slices.clear()
            response.status = ContextStatus.INSUFFICIENT
            response.missing = [{"kind": "response_budget"}]
            continue
        return


def _ambiguous_response(
    *,
    request: ContextRequest,
    intent: str,
    candidates: Sequence[dict[str, Any]],
    freshness: FreshnessResult,
    revision: int,
    cache_state: str,
    plan: dict[str, Any],
    started: float,
) -> ContextResponse:
    response = ContextResponse(
        schema_version=ADAPTIVE_SCHEMA_VERSION,
        status=ContextStatus.AMBIGUOUS,
        intent=intent,
        target=None,
        slices=[],
        candidates=[
            {
                "id": row["id"],
                "name": row["name"],
                "kind": row["kind"],
                "path": row["path"],
                "lines": _line_range(row),
            }
            for row in candidates[:3]
        ],
        freshness={
            "state": freshness.state,
            "revision": revision,
            "refreshed_files": freshness.refreshed_files,
        },
        usage={
            "tokens": 0,
            "budget": request.token_budget,
            "encoding": request.encoding,
            "latency_ms": 0.0,
            "cache": cache_state,
        },
        next={
            "tool": "csegraph_context",
            "reason": "Retry with one candidate ID as target.",
        },
        warnings=list(freshness.warnings),
    )
    if request.response_mode == "diagnostic":
        response.diagnostic = {
            "plan": plan.get("plan_mode"),
            "confidence": plan.get("confidence"),
            "score_margin": plan.get("margin"),
            "semantic_fallback": bool(plan.get("semantic_used")),
        }
    _finalize_response(response, request, started)
    return response


def _plan_cache_key(
    revision: int,
    task: str,
    target: str | None,
    intent: str,
    embedding_available: bool,
) -> str:
    raw = json.dumps(
        {
            "revision": revision,
            "task": " ".join(task.lower().split()),
            "target": (target or "").strip().lower(),
            "intent": intent,
            "engine": ADAPTIVE_ENGINE_VERSION,
            "embedding": embedding_available,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_cached_plan(
    index: ProjectIndex,
    cache_key: str,
    revision: int,
) -> dict[str, Any] | None:
    cutoff = time.time() - PLAN_CACHE_TTL_SECONDS
    row = index.conn.execute(
        """
        SELECT plan_json
        FROM retrieval_plan_cache
        WHERE cache_key = ? AND index_revision = ? AND created_at >= ?
        """,
        (cache_key, revision, cutoff),
    ).fetchone()
    if row is None:
        return None
    index.conn.execute(
        """
        UPDATE retrieval_plan_cache
        SET last_used_at = ?, hit_count = hit_count + 1
        WHERE cache_key = ?
        """,
        (time.time(), cache_key),
    )
    index.conn.commit()
    try:
        value = json.loads(row["plan_json"])
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _store_cached_plan(
    index: ProjectIndex,
    cache_key: str,
    revision: int,
    plan: dict[str, Any],
) -> None:
    now = time.time()
    index.conn.execute(
        """
        INSERT OR REPLACE INTO retrieval_plan_cache(
            cache_key, index_revision, plan_json, created_at, last_used_at, hit_count
        )
        VALUES(?, ?, ?, ?, ?, 0)
        """,
        (cache_key, revision, json.dumps(plan, sort_keys=True), now, now),
    )
    index.conn.execute(
        "DELETE FROM retrieval_plan_cache WHERE created_at < ?",
        (now - PLAN_CACHE_TTL_SECONDS,),
    )
    count = int(
        index.conn.execute("SELECT COUNT(*) AS c FROM retrieval_plan_cache").fetchone()["c"]
    )
    overflow = count - PLAN_CACHE_LIMIT
    if overflow > 0:
        index.conn.execute(
            """
            DELETE FROM retrieval_plan_cache
            WHERE cache_key IN (
                SELECT cache_key FROM retrieval_plan_cache
                ORDER BY last_used_at ASC
                LIMIT ?
            )
            """,
            (overflow,),
        )
    index.conn.commit()


def _embedding_available(index: ProjectIndex) -> bool:
    if importlib.util.find_spec("sentence_transformers") is None:
        return False
    row = index.conn.execute("SELECT 1 FROM embedding_cache LIMIT 1").fetchone()
    return row is not None


def _semantic_candidate_ids(db_path: str, task: str, limit: int) -> list[str]:
    try:
        from csegraph._core.graph.embeddings import EmbeddingService

        result = EmbeddingService(db_path).search(task, top_k=max(1, limit), hybrid=False)
        return [str(hit.node_id) for hit in result.hits]
    except Exception:
        return []


def _merge_semantic_candidates(
    index: ProjectIndex,
    ranked: list[dict[str, Any]],
    semantic_ids: Sequence[str],
) -> list[dict[str, Any]]:
    existing = {str(row["id"]): row for row in ranked}
    loaded = _load_candidate_rows(index, semantic_ids)
    for position, node_id in enumerate(semantic_ids):
        if node_id in existing or node_id not in loaded:
            continue
        row = loaded[node_id]
        row["_score"] = max(1.0, 12.0 - position * 0.25)
        existing[node_id] = row
    return sorted(
        existing.values(),
        key=lambda row: (-float(row.get("_score", 0.0)), str(row["path"])),
    )[:MAX_CANDIDATES]


def _cursor_emitted_slices(
    index: ProjectIndex,
    cursor: str | None,
    revision: int,
) -> set[tuple[str, int, int]]:
    if not cursor:
        return set()
    rows = index.conn.execute(
        """
        SELECT rc.source_hash, rc.start_line, rc.end_line
        FROM retrieval_runs rr
        JOIN retrieval_context rc ON rc.run_id = rr.id
        WHERE rr.cursor = ? AND rr.index_revision = ?
        """,
        (cursor, revision),
    ).fetchall()
    return {
        (
            str(row["source_hash"] or ""),
            int(row["start_line"] or 0),
            int(row["end_line"] or 0),
        )
        for row in rows
    }


def _record_adaptive_run(
    index: ProjectIndex,
    request: ContextRequest,
    response: ContextResponse,
    revision: int,
    rows: Sequence[dict[str, Any]],
) -> None:
    target_id = response.target.id if response.target is not None else None
    sufficient = response.status == ContextStatus.READY
    cursor = response.cursor or uuid.uuid4().hex
    cur = index.conn.execute(
        """
        INSERT INTO retrieval_runs(
            query, target, profile,
            dependency_completeness, entity_coverage, semantic_overlap,
            model_confidence, sufficient, engine, index_revision,
            response_tokens, cursor, created_at
        )
        VALUES(?, ?, 'adaptive', 0, 0, 0, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            request.task,
            target_id,
            response.target.confidence if response.target is not None else 0.0,
            1 if sufficient else 0,
            ADAPTIVE_ENGINE_VERSION,
            revision,
            int(response.usage.get("tokens") or 0),
            cursor,
            time.time(),
        ),
    )
    run_id = int(cur.lastrowid or 0)
    context_rows = []
    slice_roles = {item.id: item.role for item in response.slices if item.id}
    for rank, row in enumerate(rows, start=1):
        node_id = str(row["id"])
        context_rows.append(
            (
                run_id,
                node_id,
                rank,
                float(row.get("_score", 0.0)),
                1,
                json.dumps([slice_roles.get(node_id, "context")]),
                str(row.get("source_hash") or ""),
                row.get("start_line"),
                row.get("end_line"),
            )
        )
    if context_rows:
        index.conn.executemany(
            """
            INSERT OR REPLACE INTO retrieval_context(
                run_id, node_id, rank, score, raw_code, evidence,
                source_hash, start_line, end_line
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            context_rows,
        )
    index.conn.commit()
