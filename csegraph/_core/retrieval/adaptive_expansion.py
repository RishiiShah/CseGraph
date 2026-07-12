from __future__ import annotations

import ast
import json
import re
import textwrap
import time
from typing import Any, Sequence

from csegraph._core.core.models import (
    ContextRequest,
    ContextResponse,
    ContextSlice,
    ContextStatus,
    ContextTarget,
)
from csegraph._core.index.repository import ProjectIndex
from csegraph._core.retrieval.freshness import FreshnessResult
from csegraph._core.retrieval.token_budget import (
    DEFAULT_ENCODING,
    count_payload_tokens,
    response_tokens,
)
from csegraph._core.text.source_reader import read_source_lines

from .adaptive_caps import HARD_MAX_CANDIDATES
from .adaptive_constants import (
    _IMPACT_RELATIONS,
    ADAPTIVE_SCHEMA_VERSION,
)
from .adaptive_discovery import (
    _binding_target_ids,
    _load_candidate_rows,
    _symbol_ids_in_file,
)
from .adaptive_ranking import _deduplicate_ranked_rows, _role_rank


def _expand_one_hop(
    index: ProjectIndex,
    ranked: list[dict[str, Any]],
    target_id: str,
    intent: str,
    *,
    candidate_limit: int,
    relationship_limit: int,
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
            LIMIT ?
            """,
            (target_id, target_id, relationship_limit),
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
        if (
            role == "test"
            or current is None
            or _role_rank(role, intent) < _role_rank(current, intent)
        ):
            roles[other] = role

    neighbor_ids = list(dict.fromkeys(neighbor_ids))
    loaded = _load_candidate_rows(index, neighbor_ids)
    for node_id, row in loaded.items():
        if node_id == target_id:
            continue
        path = str(row.get("path") or "")
        if bool(row.get("is_test")) or path.startswith("tests/") or "/tests/" in path:
            roles[node_id] = "test"
    existing = {str(row["id"]): row for row in ranked}
    base_score = float(ranked[0].get("_score", 0.0)) if ranked else 1.0
    for position, node_id in enumerate(neighbor_ids):
        candidate_row = loaded.get(node_id)
        if candidate_row is None:
            continue
        role = roles.get(node_id, "context")
        bonus = {"test": 8.0, "dependency": 7.0, "caller": 6.0}.get(role, 4.0)
        if intent == "debug" and role == "test":
            bonus += 4.0
        merged = dict(existing.get(node_id, {}))
        merged.update(candidate_row)
        candidate_row["_score"] = base_score - 10.0 + bonus - position * 0.01
        candidate_row["_rank_position"] = len(ranked) + position
        candidate_row["_rank_reasons"] = [
            f"one-hop {role}",
            *(
                ["single-candidate indirect reference"]
                if any(
                    edge.get("inferred") and node_id in {str(edge["source"]), str(edge["target"])}
                    for edge in inferred_edges
                )
                else []
            ),
        ]
        merged["_score"] = candidate_row["_score"]
        merged["_rank_position"] = candidate_row["_rank_position"]
        merged["_rank_reasons"] = candidate_row["_rank_reasons"]
        merged["_scope_rank"] = 0
        merged["_graph_rank"] = 0
        merged["_penalty_rank"] = 0
        existing[node_id] = merged
    expanded = sorted(
        existing.values(),
        key=lambda row: (
            0 if str(row["id"]) == target_id else 1,
            _role_rank(roles.get(str(row["id"]), "context"), intent),
            int(row.get("_rank_position", HARD_MAX_CANDIDATES)),
            -float(row.get("_score", 0.0)),
            str(row["path"]),
        ),
    )
    return _deduplicate_ranked_rows(expanded)[:candidate_limit], roles, edges


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
        SELECT f.language, s.file_id, f.path, s.start_line, s.end_line
        FROM symbols AS s
        JOIN files AS f ON f.id = s.file_id
        WHERE s.id = ?
        LIMIT 1
        """,
        (target_id,),
    ).fetchone()
    if row is None:
        return []
    root_dir = str(index.metadata().get("root_dir") or "")
    source = read_source_lines(root_dir, str(row["path"]), 1, 10_000) if root_dir else None
    if not source:
        return []

    references: list[tuple[str, str, str]] = []
    if str(row["language"]).lower() == "python":
        try:
            tree = ast.parse(textwrap.dedent(source))
        except (SyntaxError, ValueError):
            return []
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
                        (
                            call.args[1].value,
                            "dispatches",
                            f'getattr(..., "{call.args[1].value}")',
                        )
                    )
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Subscript):
                literal_key = _literal_string(node.func.slice)
                if literal_key:
                    references.append((literal_key, "dispatches", f'...["{literal_key}"](...)'))
            if isinstance(node, ast.Dict):
                for dict_key, dict_value in zip(node.keys, node.values, strict=True):
                    if not (isinstance(dict_key, ast.Constant) and isinstance(dict_key.value, str)):
                        continue
                    symbol_name = _ast_symbol_name(dict_value)
                    if symbol_name:
                        references.append((symbol_name, "registers", dict_key.value))
    else:
        for body in re.findall(
            r"(?:const|let|var)\s+\w+\s*=\s*\{(.*?)\}\s*;",
            source,
            flags=re.DOTALL,
        ):
            for key, symbol_name in re.findall(
                r"([A-Za-z_$][\w$]*)\s*:\s*([A-Za-z_$][\w$]*)",
                body,
            ):
                references.append((symbol_name, "registers", key))

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
        bound.update(_binding_target_ids(index, dict(binding), visited=set(), depth=0))
    if bound:
        return sorted(bound)
    return _exact_symbol_name_candidates(index, name)


def _exact_symbol_name_candidates(index: ProjectIndex, name: str) -> list[str]:
    rows = index.conn.execute(
        """
        SELECT s.id
        FROM symbols AS s
        JOIN files AS f ON f.id = s.file_id
        WHERE LOWER(s.name) = LOWER(?)
           OR LOWER(s.name) LIKE '%.' || LOWER(?)
        ORDER BY f.path, s.start_line
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


def _response_within_limits(response: ContextResponse, request: ContextRequest) -> bool:
    return _content_response_tokens(response) <= request.token_budget


def _required_target_budget(
    response: ContextResponse,
    target_slice: ContextSlice,
    request: ContextRequest,
) -> int:
    response.slices.append(target_slice)
    try:
        return _content_response_tokens(response)
    finally:
        response.slices.pop()


def _response_payload_without_self_count(response: ContextResponse) -> dict[str, Any]:
    from csegraph._core.core.serializer import to_dict

    return to_dict(response)


def _content_response_tokens(response: ContextResponse) -> int:
    """Count context content without optional diagnostic metadata."""

    payload = _response_payload_without_self_count(response)
    payload.pop("diagnostics", None)
    return count_payload_tokens(payload, DEFAULT_ENCODING)


def _finalize_response(
    response: ContextResponse,
    request: ContextRequest,
    started: float,
) -> None:
    if response.diagnostics is not None:
        usage = response.diagnostics.get("usage")
        if isinstance(usage, dict):
            usage["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
            usage["tokens"] = 0
            for _ in range(8):
                tokens = response_tokens(response)
                if usage["tokens"] == tokens:
                    break
                usage["tokens"] = tokens
    while True:
        tokens = response_tokens(response)
        if tokens <= request.token_budget:
            return
        if response.diagnostics is not None:
            response.diagnostics = None
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
        if response.missing and any(len(item) > 1 for item in response.missing):
            response.missing = [
                {"kind": str(item.get("kind") or "response_budget")} for item in response.missing
            ]
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
        next={
            "tool": "csegraph_context",
            "reason": "Retry with one candidate ID as target.",
        },
        warnings=list(freshness.warnings),
    )
    if request.diagnostic:
        response.diagnostics = {
            "target": None,
            "intent": intent,
            "plan": plan.get("plan_mode"),
            "confidence": plan.get("confidence"),
            "score_margin": plan.get("margin"),
            "caps": dict(plan.get("caps") or {}),
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
    _finalize_response(response, request, started)
    return response
