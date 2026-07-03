from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import textwrap
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
    token_estimator,
    token_measurement,
    validate_token_budget,
)
from csegraph._core.text.query_tokenizer import query_tokenizer
from csegraph._core.text.source_reader import read_source_lines

ADAPTIVE_SCHEMA_VERSION = "csegraph-context-v4"
ADAPTIVE_ENGINE_VERSION = "adaptive-v2"
MAX_CANDIDATES = 64
MAX_SLICES = 5
PLAN_CACHE_LIMIT = 1_000
PLAN_CACHE_TTL_SECONDS = 24 * 60 * 60
TARGET_CONFIDENCE_THRESHOLD = 0.75
TARGET_MARGIN_THRESHOLD = 0.15
EMBEDDING_FALLBACK_THRESHOLD = 0.65

_IMPACT_RELATIONS = {
    "calls",
    "decorates",
    "dispatches",
    "imports",
    "inherits",
    "registers",
    "tested_by",
}
_GENERATED_PATH_PARTS = {
    ".generated",
    ".venv",
    "build",
    "dist",
    "generated",
    "node_modules",
    "third_party",
    "vendor",
}

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
                    "rank_evidence": {
                        str(row["id"]): _candidate_evidence(row)
                        for row in ranked[:MAX_CANDIDATES]
                    },
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
            rank_evidence = cached_plan.get("rank_evidence") or {}
            for position, row in enumerate(ordered):
                row["_rank_position"] = position
                evidence = rank_evidence.get(str(row["id"]))
                if isinstance(evidence, dict):
                    row["_score"] = float(evidence.get("score") or 0.0)
                    row["_rank_reasons"] = list(evidence.get("reasons") or [])
                    row["_precedence"] = _evidence_int(evidence, "precedence", 9)
                    row["_penalty_rank"] = _evidence_int(evidence, "penalty_rank", 0)
                    row["_fts_rank"] = _evidence_int(
                        evidence,
                        "fts_rank",
                        MAX_CANDIDATES,
                    )
                    row["_scope_rank"] = _evidence_int(evidence, "scope_rank", 2)
                    row["_graph_rank"] = _evidence_int(evidence, "graph_rank", 1)
                    row["_semantic_rank"] = _evidence_int(
                        evidence,
                        "semantic_rank",
                        MAX_CANDIDATES,
                    )
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
                    "estimator": token_estimator(request.encoding),
                    "measurement": token_measurement(request.encoding),
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
                            "rank": int(row.get("_rank_position", 0)) + 1,
                            "score": round(float(row.get("_score", 0.0)), 4),
                            "reasons": list(row.get("_rank_reasons") or []),
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
            "estimator": token_estimator(request.encoding),
            "measurement": token_measurement(request.encoding),
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
    if intent in {"edit", "debug", "review"}:
        return "impact"
    if tokens & _STRUCTURAL_WORDS:
        return "structural"
    return "lexical"


def _discover_candidates(
    index: ProjectIndex,
    task: str,
    target: str | None,
) -> tuple[list[dict[str, Any]], bool]:
    candidate_scores: dict[str, float] = {}
    candidate_precedence: dict[str, int] = {}
    candidate_fts_rank: dict[str, int] = {}
    candidate_reasons: dict[str, list[str]] = {}
    exact_ids: set[str] = set()
    target_exact = False
    target_rows: list[dict[str, Any]] = []
    if target:
        target_rows, exact = _target_candidate_rows(index, target)
        target_exact = exact
        for position, row in enumerate(target_rows):
            node_id = str(row["id"])
            candidate_scores[node_id] = 100.0 - position
            candidate_precedence[node_id] = 0 if exact else 1
            candidate_reasons[node_id] = [
                "explicit exact target" if exact else "explicit target candidate"
            ]
            if exact:
                exact_ids.add(node_id)

    fts_ids = _fts_candidate_ids(index, task, MAX_CANDIDATES)
    for position, node_id in enumerate(fts_ids):
        candidate_scores[node_id] = max(
            candidate_scores.get(node_id, 0.0),
            30.0 - (position / max(1, len(fts_ids))) * 10.0,
        )
        candidate_precedence[node_id] = min(candidate_precedence.get(node_id, 9), 3)
        candidate_fts_rank[node_id] = min(
            candidate_fts_rank.get(node_id, MAX_CANDIDATES),
            position,
        )
        candidate_reasons.setdefault(node_id, []).append("full-text match")

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
            candidate_precedence[node_id] = min(candidate_precedence.get(node_id, 9), 2)
            candidate_reasons.setdefault(node_id, []).append("exact task symbol")
            exact_ids.add(node_id)

    rows_by_id = _load_candidate_rows(index, candidate_scores.keys())
    task_lower = task.lower()
    explicit_target = (target or "").lower()
    task_token_set = set(task_tokens)
    scope_file_ids, imported_file_ids = _task_scope_file_ids(index, task)
    anchor_ids = (
        [str(row["id"]) for row in target_rows]
        if target_rows
        else sorted(exact_ids)
    )
    if not anchor_ids and fts_ids:
        anchor_ids = fts_ids[:1]
    graph_near_ids = _directly_connected_candidate_ids(
        index,
        anchor_ids,
        rows_by_id.keys(),
    )
    for node_id, row in rows_by_id.items():
        score = candidate_scores.get(node_id, 0.0)
        name = str(row["name"]).lower()
        path = str(row["path"]).lower()
        if name and name in task_lower:
            score += 20.0
            candidate_reasons.setdefault(node_id, []).append("name mentioned in task")
        if path and path in task_lower:
            score += 15.0
            candidate_reasons.setdefault(node_id, []).append("path mentioned in task")
        if explicit_target and (
            name == explicit_target or path == explicit_target or node_id.lower() == explicit_target
        ):
            score += 50.0
            candidate_precedence[node_id] = 0
            candidate_reasons.setdefault(node_id, []).append("explicit target match")
            exact_ids.add(node_id)
        overlap = set(query_tokenizer.tokenize(f"{name} {path}")) & task_token_set
        score += float(len(overlap)) * 2.0
        file_id = str(row.get("file_id") or "")
        if file_id in scope_file_ids:
            row["_scope_rank"] = 0
            score += 18.0
            candidate_reasons.setdefault(node_id, []).append("same-file task scope")
        elif file_id in imported_file_ids:
            row["_scope_rank"] = 1
            score += 12.0
            candidate_reasons.setdefault(node_id, []).append("imported task scope")
        else:
            row["_scope_rank"] = 2
        if node_id in graph_near_ids:
            row["_graph_rank"] = 0
            score += 6.0
            candidate_reasons.setdefault(node_id, []).append("direct candidate relationship")
        else:
            row["_graph_rank"] = 1
        explicitly_requested = candidate_precedence.get(node_id, 9) == 0
        if bool(row.get("is_test")) and not (task_token_set & _TEST_WORDS) and not explicitly_requested:
            score -= 24.0
            row["_penalty_rank"] = 1
            candidate_reasons.setdefault(node_id, []).append("test-symbol penalty")
        else:
            row["_penalty_rank"] = 0
        if _is_generated_or_vendor_path(path) and not explicitly_requested:
            score -= 20.0
            row["_penalty_rank"] = max(int(row["_penalty_rank"]), 1)
            candidate_reasons.setdefault(node_id, []).append("generated/vendor penalty")
        row["_score"] = score
        row["_precedence"] = candidate_precedence.get(node_id, 9)
        row["_fts_rank"] = candidate_fts_rank.get(node_id, MAX_CANDIDATES)
        row["_semantic_rank"] = MAX_CANDIDATES
        row["_rank_reasons"] = list(dict.fromkeys(candidate_reasons.get(node_id, [])))

    ranked = sorted(rows_by_id.values(), key=_candidate_sort_key)
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
    return _deduplicate_ranked_rows(ranked)[:MAX_CANDIDATES], exact


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

    reexported = _reexport_target_candidates(index, requested)
    if reexported:
        return [{"id": node_id} for node_id in reexported], len(reexported) == 1

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


def _reexport_target_candidates(index: ProjectIndex, requested: str) -> list[str]:
    """Resolve an import alias to its canonical indexed symbol.

    Re-export chains are followed through resolved files, with a small depth cap
    and cycle guard. Multiple canonical destinations stay ambiguous.
    """

    terminal = requested.rsplit(".", 1)[-1]
    rows = index.conn.execute(
        """
        SELECT file_id, local_name, imported_name, qualified_name,
               resolved_file_id, resolved_symbol_id
        FROM import_bindings
        WHERE LOWER(local_name) = LOWER(?)
           OR LOWER(qualified_name) = LOWER(?)
        ORDER BY file_id, start_line, local_name
        LIMIT 16
        """,
        (terminal, requested),
    ).fetchall()
    candidates: set[str] = set()
    for row in rows:
        candidates.update(_binding_target_ids(index, dict(row), visited=set(), depth=0))
    return sorted(candidates)


def _binding_target_ids(
    index: ProjectIndex,
    binding: dict[str, Any],
    *,
    visited: set[tuple[str, str]],
    depth: int,
) -> set[str]:
    resolved_symbol = str(binding.get("resolved_symbol_id") or "")
    if resolved_symbol:
        return {resolved_symbol}
    resolved_file = str(binding.get("resolved_file_id") or "")
    imported_name = str(binding.get("imported_name") or "")
    if not resolved_file or not imported_name or depth >= 4:
        return set()
    visit_key = (resolved_file, imported_name.casefold())
    if visit_key in visited:
        return set()
    next_visited = {*visited, visit_key}

    direct = set(_symbol_ids_in_file(index, resolved_file, imported_name))
    if direct:
        return direct

    rows = index.conn.execute(
        """
        SELECT file_id, local_name, imported_name, qualified_name,
               resolved_file_id, resolved_symbol_id
        FROM import_bindings
        WHERE file_id = ? AND LOWER(local_name) = LOWER(?)
        ORDER BY start_line, local_name
        LIMIT 8
        """,
        (resolved_file, imported_name),
    ).fetchall()
    candidates: set[str] = set()
    for row in rows:
        candidates.update(
            _binding_target_ids(
                index,
                dict(row),
                visited=next_visited,
                depth=depth + 1,
            )
        )
    return candidates


def _symbol_ids_in_file(index: ProjectIndex, file_id: str, name: str) -> list[str]:
    rows = index.conn.execute(
        """
        SELECT id
        FROM symbols
        WHERE file_id = ?
          AND (
              LOWER(name) = LOWER(?)
              OR LOWER(name) LIKE '%.' || LOWER(?)
          )
        ORDER BY path, start_line, id
        LIMIT 3
        """,
        (file_id, name, name),
    ).fetchall()
    return [str(row["id"]) for row in rows]


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
            s.id, s.file_id, s.parent_id, s.kind, s.name, s.path, s.language, s.signature,
            s.docstring, s.start_line, s.end_line, s.source_hash, s.is_test,
            COALESCE(sm.summary, '') AS summary
        FROM symbols s
        LEFT JOIN summaries sm ON sm.node_id = s.id
        WHERE s.id IN ({placeholders})
        """,
        tuple(unique),
    ).fetchall()
    return {str(row["id"]): dict(row) for row in rows}


def _task_scope_file_ids(
    index: ProjectIndex,
    task: str,
) -> tuple[set[str], set[str]]:
    task_lower = task.lower().replace("\\", "/")
    scoped: set[str] = set()
    for row in index.conn.execute("SELECT id, path FROM files ORDER BY path"):
        path = str(row["path"]).lower()
        basename = path.rsplit("/", 1)[-1]
        if path in task_lower or basename in task_lower:
            scoped.add(str(row["id"]))
    if not scoped:
        return set(), set()
    placeholders = ",".join("?" for _ in scoped)
    imported = {
        str(row["resolved_file_id"])
        for row in index.conn.execute(
            f"""
            SELECT DISTINCT resolved_file_id
            FROM imports
            WHERE file_id IN ({placeholders}) AND resolved_file_id IS NOT NULL
            """,
            tuple(sorted(scoped)),
        )
    }
    return scoped, imported


def _directly_connected_candidate_ids(
    index: ProjectIndex,
    anchor_ids: Iterable[str],
    node_ids: Iterable[str],
) -> set[str]:
    anchors = list(dict.fromkeys(str(node_id) for node_id in anchor_ids if node_id))
    candidates = {str(node_id) for node_id in node_ids if node_id}
    if not anchors or len(candidates) < 2:
        return set()
    placeholders = ",".join("?" for _ in anchors)
    rows = index.conn.execute(
        f"""
        SELECT source, target
        FROM edges
        WHERE (source IN ({placeholders}) OR target IN ({placeholders}))
          AND relation IN ('calls', 'decorates', 'imports', 'inherits', 'tested_by')
        ORDER BY source, target, relation
        LIMIT 128
        """,
        (*anchors, *anchors),
    ).fetchall()
    connected: set[str] = set()
    for row in rows:
        source = str(row["source"])
        target = str(row["target"])
        if source in anchors and target in candidates:
            connected.add(target)
        if target in anchors and source in candidates:
            connected.add(source)
    return connected


def _is_generated_or_vendor_path(path: str) -> bool:
    normalized = path.lower().replace("\\", "/")
    parts = {part for part in normalized.split("/") if part}
    filename = normalized.rsplit("/", 1)[-1]
    return bool(
        parts & _GENERATED_PATH_PARTS
        or ".generated." in filename
        or filename.endswith((".g.py", ".g.ts", ".min.js"))
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
        "semantic_rank": int(row.get("_semantic_rank", MAX_CANDIDATES)),
        "reasons": list(row.get("_rank_reasons") or []),
    }


def _evidence_int(evidence: dict[str, Any], key: str, default: int) -> int:
    value = evidence.get(key)
    return default if value is None else int(value)


def _candidate_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """Keep public ranking precedence explicit and deterministic.

    Test/generated penalties apply before discovery rank unless the symbol was
    explicitly requested. FTS and semantic positions are only meaningful inside
    their own discovery tier.
    """

    precedence = int(row.get("_precedence", 9))
    return (
        precedence,
        int(row.get("_penalty_rank", 0)),
        int(row.get("_fts_rank", MAX_CANDIDATES)) if precedence == 3 else 0,
        int(row.get("_scope_rank", 2)),
        int(row.get("_graph_rank", 1)),
        int(row.get("_semantic_rank", MAX_CANDIDATES)) if precedence >= 4 else 0,
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


def _expand_one_hop(
    index: ProjectIndex,
    ranked: list[dict[str, Any]],
    target_id: str,
    intent: str,
) -> tuple[list[dict[str, Any]], dict[str, str], list[dict[str, Any]]]:
    direct_edges = [
        dict(row)
        for row in index.conn.execute(
            """
            SELECT source, target, relation, confidence, confidence_tier
            FROM edges
            WHERE (source = ? OR target = ?)
              AND relation IN (
                  'calls', 'decorates', 'imports', 'inherits', 'tested_by'
              )
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
    inferred_edges = _inferred_occurrence_edges(index, target_id)
    inferred_edges.extend(_literal_dispatch_edges(index, target_id))
    edges = _deduplicate_relationships([*direct_edges, *inferred_edges])
    neighbor_ids: list[str] = []
    roles = {target_id: "target"}
    for edge in edges:
        outgoing = edge["source"] == target_id
        other = str(edge["target"] if outgoing else edge["source"])
        if not other or other == target_id:
            continue
        neighbor_ids.append(other)
        role = _relationship_role(edge, target_id)
        current = roles.get(other)
        if current is None or _role_rank(role, intent) < _role_rank(current, intent):
            roles[other] = role

    neighbor_ids = list(dict.fromkeys(neighbor_ids))
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
        row["_rank_position"] = len(ranked) + position
        row["_rank_reasons"] = [
            f"one-hop {role}",
            *(
                ["single-candidate indirect reference"]
                if any(
                    edge.get("inferred")
                    and node_id in {str(edge["source"]), str(edge["target"])}
                    for edge in inferred_edges
                )
                else []
            ),
        ]
        existing.setdefault(node_id, row)
    expanded = sorted(
        existing.values(),
        key=lambda row: (
            0 if str(row["id"]) == target_id else 1,
            _role_rank(roles.get(str(row["id"]), "context"), intent),
            int(row.get("_rank_position", MAX_CANDIDATES)),
            -float(row.get("_score", 0.0)),
            str(row["path"]),
        ),
    )
    return _deduplicate_ranked_rows(expanded)[:MAX_CANDIDATES], roles, edges


def _inferred_occurrence_edges(
    index: ProjectIndex,
    target_id: str,
) -> list[dict[str, Any]]:
    """Promote only unambiguous indexed occurrence candidates.

    The parser deliberately leaves attribute calls unresolved when it cannot infer
    the receiver type. A single indexed candidate is still useful retrieval
    evidence, but it must remain visibly inferred rather than becoming a graph fact.
    """

    rows = index.conn.execute(
        """
        SELECT relation, name, source_text, source_file_id, resolution_status,
               resolution_strategy, candidate_targets
        FROM edge_occurrences
        WHERE source = ?
          AND target IS NULL
          AND is_stale = 0
          AND relation IN ('calls', 'decorates', 'inherits', 'tested_by')
        ORDER BY start_line, end_line, name
        LIMIT 48
        """,
        (target_id,),
    ).fetchall()
    inferred: list[dict[str, Any]] = []
    for row in rows:
        candidates = _json_string_list(row["candidate_targets"])
        strategy = "single_indexed_candidate"
        if not candidates:
            reference_name = _reference_terminal_name(str(row["name"] or ""))
            candidates = _unique_reference_candidates(
                index,
                reference_name,
                str(row["source_file_id"] or ""),
            )
            strategy = "unique_scoped_symbol_reference"
        if len(candidates) != 1 or candidates[0] == target_id:
            continue
        candidate = index.conn.execute(
            "SELECT id FROM symbols WHERE id = ? LIMIT 1",
            (candidates[0],),
        ).fetchone()
        if candidate is None:
            continue
        inferred.append(
            {
                "source": target_id,
                "target": str(candidate["id"]),
                "relation": str(row["relation"]),
                "confidence": 0.65,
                "confidence_tier": "INFERRED",
                "inferred": True,
                "resolution_status": str(row["resolution_status"]),
                "resolution_strategy": (
                    str(row["resolution_strategy"])
                    if strategy == "single_indexed_candidate"
                    else strategy
                ),
                "evidence": str(row["source_text"] or row["name"]),
            }
        )
    return inferred


def _literal_dispatch_edges(
    index: ProjectIndex,
    target_id: str,
) -> list[dict[str, Any]]:
    row = index.conn.execute(
        """
        SELECT language, file_id, path, start_line, end_line
        FROM symbols
        WHERE id = ?
        LIMIT 1
        """,
        (target_id,),
    ).fetchone()
    if row is None or str(row["language"]).lower() != "python":
        return []
    root_dir = str(index.metadata().get("root_dir") or "")
    source = _read_symbol_source(root_dir, dict(row)) if root_dir else None
    if not source:
        return []
    try:
        tree = ast.parse(textwrap.dedent(source))
    except (SyntaxError, ValueError):
        return []

    references: list[tuple[str, str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Call):
            call = node.func
            if (
                isinstance(call.func, ast.Name)
                and call.func.id == "getattr"
                and len(call.args) >= 2
                and isinstance(call.args[1], ast.Constant)
                and isinstance(call.args[1].value, str)
            ):
                references.append(
                    (call.args[1].value, "dispatches", f'getattr(..., "{call.args[1].value}")')
                )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Subscript):
            literal_key = _literal_string(node.func.slice)
            if literal_key:
                references.append(
                    (literal_key, "dispatches", f'...["{literal_key}"](...)')
                )
        if isinstance(node, ast.Dict):
            for dict_key, dict_value in zip(node.keys, node.values, strict=True):
                if not (
                    isinstance(dict_key, ast.Constant)
                    and isinstance(dict_key.value, str)
                ):
                    continue
                symbol_name = _ast_symbol_name(dict_value)
                if symbol_name:
                    references.append((symbol_name, "registers", dict_key.value))

    inferred: list[dict[str, Any]] = []
    for name, relation, evidence in references:
        candidates = _unique_reference_candidates(index, name, str(row["file_id"]))
        if len(candidates) != 1 or candidates[0] == target_id:
            continue
        inferred.append(
            {
                "source": target_id,
                "target": candidates[0],
                "relation": relation,
                "confidence": 0.55,
                "confidence_tier": "INFERRED",
                "inferred": True,
                "resolution_status": "inferred",
                "resolution_strategy": "unique_literal_symbol",
                "evidence": evidence,
            }
        )
    return inferred


def _ast_symbol_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _literal_string(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _reference_terminal_name(name: str) -> str:
    return name.rsplit(".", 1)[-1].strip()


def _unique_reference_candidates(
    index: ProjectIndex,
    name: str,
    source_file_id: str,
) -> list[str]:
    """Resolve a weak dynamic reference only when one canonical target remains."""

    if not name:
        return []
    local = _symbol_ids_in_file(index, source_file_id, name)
    if local:
        return local if len(local) > 1 else [local[0]]

    binding_rows = index.conn.execute(
        """
        SELECT file_id, local_name, imported_name, qualified_name,
               resolved_file_id, resolved_symbol_id
        FROM import_bindings
        WHERE file_id = ? AND LOWER(local_name) = LOWER(?)
        ORDER BY start_line, local_name
        LIMIT 8
        """,
        (source_file_id, name),
    ).fetchall()
    bound: set[str] = set()
    for binding in binding_rows:
        bound.update(
            _binding_target_ids(index, dict(binding), visited=set(), depth=0)
        )
    if bound:
        return sorted(bound)
    return _exact_symbol_name_candidates(index, name)


def _exact_symbol_name_candidates(index: ProjectIndex, name: str) -> list[str]:
    rows = index.conn.execute(
        """
        SELECT id
        FROM symbols
        WHERE LOWER(name) = LOWER(?)
           OR LOWER(name) LIKE '%.' || LOWER(?)
        ORDER BY path, start_line
        LIMIT 2
        """,
        (name, name),
    ).fetchall()
    return [str(row["id"]) for row in rows]


def _json_string_list(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
        return []
    return list(dict.fromkeys(parsed))


def _relationship_role(edge: dict[str, Any], target_id: str) -> str:
    relation = str(edge.get("relation") or "")
    source = str(edge.get("source") or "")
    target = str(edge.get("target") or "")
    if relation == "tested_by":
        return "test"
    if relation in {"calls", "dispatches", "registers", "inherits"}:
        return "dependency" if source == target_id else "caller"
    if relation == "decorates":
        return "dependency" if target == target_id else "caller"
    return "dependency" if source == target_id else "caller"


def _deduplicate_relationships(
    edges: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for edge in edges:
        relation = str(edge.get("relation") or "")
        if relation not in _IMPACT_RELATIONS:
            continue
        key = (
            str(edge.get("source") or ""),
            str(edge.get("target") or ""),
            relation,
        )
        if not all(key) or key in seen:
            continue
        seen.add(key)
        result.append(edge)
    return result


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
            "estimator": token_estimator(request.encoding),
            "measurement": token_measurement(request.encoding),
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
        if node_id in existing:
            row = existing[node_id]
            row["_semantic_rank"] = min(
                int(row.get("_semantic_rank", MAX_CANDIDATES)),
                position,
            )
            row["_score"] = float(row.get("_score", 0.0)) + max(
                0.0,
                4.0 - position * 0.1,
            )
            row["_rank_reasons"] = list(
                dict.fromkeys([*(row.get("_rank_reasons") or []), "semantic match"])
            )
            continue
        if node_id not in loaded:
            continue
        row = loaded[node_id]
        row["_score"] = max(1.0, 12.0 - position * 0.25)
        row["_precedence"] = 4
        row["_penalty_rank"] = (
            1
            if bool(row.get("is_test"))
            or _is_generated_or_vendor_path(str(row.get("path") or ""))
            else 0
        )
        row["_fts_rank"] = MAX_CANDIDATES
        row["_scope_rank"] = 2
        row["_graph_rank"] = 1
        row["_semantic_rank"] = position
        row["_rank_reasons"] = ["semantic fallback"]
        existing[node_id] = row
    return _deduplicate_ranked_rows(
        sorted(existing.values(), key=_candidate_sort_key)
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
