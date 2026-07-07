from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from csegraph._core.core.models import (
    ContextRequest,
    ContextResponse,
    ContextSlice,
    ContextStatus,
)
from csegraph._core.index.repository import ProjectIndex
from csegraph._core.retrieval.adaptive_discovery import (
    _discover_candidates,
    _load_candidate_rows,
    _target_candidate_rows,
)
from csegraph._core.retrieval.adaptive_expansion import (
    _ambiguous_response,
    _context_target,
    _expand_one_hop,
    _finalize_response,
    _line_range,
    _read_symbol_source,
    _required_target_budget,
    _response_within_limits,
)
from csegraph._core.retrieval.adaptive_ranking import (
    _candidate_evidence,
    _evidence_int,
    _prioritize_rows,
    _slice_key,
    _target_confidence,
)
from csegraph._core.retrieval.freshness import FreshnessCoordinator, FreshnessResult
from csegraph._core.retrieval.token_budget import (
    DEFAULT_ENCODING,
    validate_token_budget,
)
from csegraph._core.text.query_tokenizer import query_tokenizer

from .adaptive_constants import (
    _DEBUG_WORDS,
    _EDIT_WORDS,
    _STRUCTURAL_WORDS,
    ADAPTIVE_SCHEMA_VERSION,
    MAX_CANDIDATES,
    MAX_SLICES,
    TARGET_CONFIDENCE_THRESHOLD,
    TARGET_MARGIN_THRESHOLD,
    TINY_REPO_FILE_LIMIT,
    TINY_REPO_SYMBOL_LIMIT,
)


class ContextService:
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
            tiny_repo = _is_tiny_repo(metadata)
            intent = _infer_intent(request.task, request.task_kind)
            plan_mode = _plan_mode(request.task, intent)
            compress_target_source = plan_mode == "impact" and intent != "edit"
            cache_state = "disabled"
            ranked: list[dict[str, Any]] = []
            exact = False
            if tiny_repo and request.target:
                fast_path = _tiny_target_candidates(index, request.target)
                if fast_path is not None:
                    ranked, exact = fast_path
            if not ranked:
                ranked, exact = _discover_candidates(index, request.task, request.target)
            slice_limit = MAX_SLICES
            confidence, margin = _target_confidence(ranked, exact=exact)
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
            plan: dict[str, Any] = {
                "ranked_ids": [str(row["id"]) for row in ranked[:MAX_CANDIDATES]],
                "rank_evidence": {
                    str(row["id"]): _candidate_evidence(row) for row in ranked[:MAX_CANDIDATES]
                },
                "roles": roles,
                "target_id": target_id,
                "confidence": confidence,
                "margin": margin,
                "exact": exact,
                "resolved": resolved,
                "plan_mode": plan_mode,
                "relationships": relationships,
            }
            rows = _load_candidate_rows(index, plan.get("ranked_ids", []))
            ordered = [rows[node_id] for node_id in plan.get("ranked_ids", []) if node_id in rows]
            rank_evidence = plan.get("rank_evidence") or {}
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
            target_id = str(plan.get("target_id") or "")
            resolved = bool(plan.get("resolved") and target_id in rows)
            confidence = float(plan.get("confidence") or 0.0)
            margin = float(plan.get("margin") or 0.0)
            roles = {str(key): str(value) for key, value in (plan.get("roles") or {}).items()}
            emitted: set[tuple[str, int, int]] = set()

            if not resolved:
                response = _ambiguous_response(
                    request=request,
                    intent=intent,
                    candidates=ordered[:3],
                    freshness=freshness,
                    revision=revision,
                    cache_state=cache_state,
                    plan=plan,
                    started=started,
                )
                return response

            target_row = rows[target_id]
            target = _context_target(target_row, confidence)
            ordered = _prioritize_rows(ordered, target_id, roles, intent)
            if plan.get("plan_mode") != "impact":
                ordered = [target_row]
            else:
                ordered = [
                    row for row in ordered if str(row["id"]) == target_id or str(row["id"]) in roles
                ]
            response = ContextResponse(
                schema_version=ADAPTIVE_SCHEMA_VERSION,
                status=ContextStatus.READY,
                slices=[],
                warnings=list(freshness.warnings),
            )
            if plan.get("plan_mode") == "structural":
                response.next = {
                    "tool": "csegraph_graph",
                    "arguments": {
                        "node": target_id,
                        "depth": 1,
                    },
                    "reason": "The task asks for structural context; inspect the focused neighborhood.",
                }
            if request.diagnostic:
                response.diagnostics = {
                    "target": {
                        "id": target.id,
                        "name": target.name,
                        "kind": target.kind,
                        "path": target.path,
                        "lines": target.lines,
                    },
                    "intent": intent,
                    "plan": plan.get("plan_mode"),
                    "confidence": round(confidence, 4),
                    "score_margin": round(margin, 4),
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
                    "relationships": list(plan.get("relationships") or []),
                    "freshness": {
                        "state": freshness.state,
                        "revision": revision,
                        "refreshed_files": freshness.refreshed_files,
                    },
                    "usage": {
                        "tokens": 0,
                        "budget": request.token_budget,
                        "encoding": DEFAULT_ENCODING,
                        "cache": cache_state,
                    },
                }

            selected_rows: list[dict[str, Any]] = []
            target_already_emitted = _slice_key(target_row) in emitted
            target_slice_required = request.source_mode != "never" and not target_already_emitted
            for row in ordered:
                if len(response.slices) >= slice_limit:
                    break
                if _slice_key(row) in emitted:
                    continue
                role = roles.get(str(row["id"]), "context")
                include_source = request.source_mode == "always" or (
                    request.source_mode != "never" and str(row["id"]) == target_id
                )
                code = ""
                if include_source:
                    source = _read_symbol_source(repo_root, row) or ""
                    if request.source_mode == "always" or str(row["id"]) != target_id:
                        code = source
                    elif not compress_target_source:
                        code = source
                    else:
                        code = source.splitlines()[0] + ("\n" if source else "")
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
                            "task": request.task,
                            "repo": request.repo,
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
            return response
        finally:
            index.close()


def _validate_request(request: ContextRequest) -> None:
    if not request.task or not request.task.strip():
        raise ValueError("task must be a non-empty string")
    validate_token_budget(request.token_budget)
    if request.source_mode not in {"auto", "always", "never"}:
        raise ValueError("source_mode must be one of: auto, always, never")
    if request.task_kind not in {"auto", "edit", "understand", "review", "test-impact"}:
        raise ValueError("task_kind must be one of: auto, edit, understand, review, test-impact")
    if not isinstance(request.diagnostic, bool):
        raise TypeError("diagnostic must be a boolean")


def _is_tiny_repo(metadata: dict[str, str]) -> bool:
    file_count = metadata.get("file_count")
    symbol_count = metadata.get("symbol_count")
    if file_count is None or symbol_count is None:
        return False
    return (
        int(file_count) <= TINY_REPO_FILE_LIMIT
        and int(symbol_count) <= TINY_REPO_SYMBOL_LIMIT
    )


def _tiny_target_candidates(
    index: ProjectIndex,
    target: str,
) -> tuple[list[dict[str, Any]], bool] | None:
    target_rows, exact = _target_candidate_rows(index, target)
    if not exact or len(target_rows) != 1:
        return None
    target_id = str(target_rows[0]["id"])
    rows = _load_candidate_rows(index, [target_id])
    if target_id not in rows:
        return None
    return [rows[target_id]], True


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
        slices=[],
        next=freshness.next,
        warnings=list(freshness.warnings),
    )
    if request.diagnostic:
        response.diagnostics = {
            "intent": _infer_intent(request.task, request.task_kind),
            "target": None,
            "freshness": {
                "state": freshness.state,
                "revision": freshness.revision,
                "refreshed_files": freshness.refreshed_files,
            },
            "usage": {
                "tokens": 0,
                "budget": request.token_budget,
                "encoding": DEFAULT_ENCODING,
                "cache": "disabled",
            },
        }
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
