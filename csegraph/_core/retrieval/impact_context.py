"""Push-style context returned directly by an index refresh.

This closes the refresh -> query gap: the same operation that updates the
database also returns the changed symbols and their cascading dependents.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from csegraph._core.core.models import ContextResponse, ContextSlice, ContextStatus
from csegraph._core.core.serializer import to_dict
from csegraph._core.index.repository import ProjectIndex
from csegraph._core.retrieval.adaptive_constants import ADAPTIVE_SCHEMA_VERSION
from csegraph._core.retrieval.token_budget import (
    DEFAULT_ENCODING,
    count_payload_tokens,
)
from csegraph._core.text.query_tokenizer import query_tokenizer
from csegraph._core.text.source_reader import read_source_lines

IMPACT_CONTEXT_TOKEN_BUDGET = 1_600
IMPACT_CONTEXT_DEPTH = 2
_IMPACT_NODE_CEILING = 160
_IMPACT_EDGE_CEILING = 400


def build_impact_context(
    index: ProjectIndex,
    repo_root: str,
    seed_symbol_ids: Iterable[str],
    *,
    token_budget: int = IMPACT_CONTEXT_TOKEN_BUDGET,
    depth: int = IMPACT_CONTEXT_DEPTH,
    task: str | None = None,
    seed_roles: Mapping[str, str] | None = None,
) -> ContextResponse | None:
    # Import after freshness initialization to avoid its coordinator import cycle.
    from csegraph._core.retrieval.adaptive_expansion import (
        _adjacent_impact_edges,
        _bridged_impact_nodes,
        _relationship_role,
    )

    # shortcut: seeds bypass expansion caps; bound selection if large refreshes become slow.
    seeds = [node_id for node_id in dict.fromkeys(seed_symbol_ids) if node_id]
    if not seeds:
        return None

    distances: dict[str, int] = {}
    roles: dict[str, str] = {}
    order: list[str] = []
    task_tokens = _task_tokens(task)
    edge_budget = _IMPACT_EDGE_CEILING
    primary_seeds = [seed for seed in seeds if (seed_roles or {}).get(seed) != "impacted"]

    # Discover direct roles before seeding dependents unreachable through symbol edges.
    for seed_group in (primary_seeds, seeds):
        frontier = [seed for seed in seed_group if seed not in distances]
        for seed in frontier:
            distances[seed] = 0
            roles[seed] = (seed_roles or {}).get(seed, "changed")
            order.append(seed)
        for level in range(1, max(1, depth) + 1):
            next_frontier: list[str] = []
            for node_id in frontier:
                if len(distances) >= _IMPACT_NODE_CEILING or edge_budget <= 0:
                    break
                rows = _adjacent_impact_edges(index, node_id, edge_budget)
                edge_budget -= len(rows)
                for row in rows:
                    source = str(row["source"])
                    target = str(row["target"])
                    outgoing = source == node_id
                    other = target if outgoing else source
                    if not other or other == node_id or other in distances:
                        continue
                    role = (
                        _relationship_role(row, node_id)
                        if level == 1 or row["relation"] == "tested_by"
                        else "cascade"
                    )
                    for candidate, _ in _bridged_impact_nodes(
                        index, other, level, _IMPACT_NODE_CEILING
                    ):
                        if candidate in distances or len(distances) >= _IMPACT_NODE_CEILING:
                            continue
                        distances[candidate] = level
                        roles[candidate] = role
                        order.append(candidate)
                        next_frontier.append(candidate)
            frontier = next_frontier
            if not frontier:
                break

    rows_by_id = _load_rows(index, order)
    ordered = [rows_by_id[node_id] for node_id in order if node_id in rows_by_id]
    task_scores = {str(row["id"]): _task_overlap(row, task_tokens) for row in ordered}
    ordered.sort(
        key=lambda row: (
            _role_priority(roles.get(str(row["id"]), "context")),
            -task_scores.get(str(row["id"]), 0),
            int(distances.get(str(row["id"]), depth + 1)),
            str(row["path"]),
            int(row.get("start_line") or 0),
        )
    )
    missing: list[dict[str, Any]] = []
    for row in ordered:
        row["_impact_role"] = roles.get(str(row["id"]), "context")

    response = ContextResponse(
        schema_version=ADAPTIVE_SCHEMA_VERSION,
        status=ContextStatus.READY,
        slices=[],
    )
    for row in ordered:
        line_range = _line_range(row)
        if line_range is None:
            continue
        code = read_source_lines(
            repo_root,
            str(row["path"]),
            line_range[0],
            line_range[1],
        )
        if not code:
            continue
        response.slices.append(
            ContextSlice(
                id=str(row["id"]),
                path=str(row["path"]),
                lines=line_range,
                symbol=str(row["name"]),
                role=str(row["_impact_role"]),
                code=code,
            )
        )
        if _response_tokens(response) > token_budget:
            required_budget = _response_tokens(response)
            response.slices.pop()
            missing.append(
                {
                    "kind": "source",
                    "path": str(row["path"]),
                    "lines": line_range,
                    "required_budget": required_budget,
                }
            )

    if missing and not response.slices:
        response.status = ContextStatus.INSUFFICIENT
    for item in missing:
        response.missing.append(item)
        if _response_tokens(response) > token_budget:
            response.missing.pop()
            break

    return response if response.slices or missing else None


def _role_priority(role: str) -> int:
    return {
        "changed": 0,
        "impacted": 1,
        "caller": 1,
        "dependency": 2,
        "cascade": 3,
        "test": 3,
    }.get(role, 4)


def _task_tokens(task: str | None) -> frozenset[str]:
    if not task:
        return frozenset()
    return frozenset(token for token in query_tokenizer.tokenize(task) if len(token) > 1)


def _task_overlap(row: dict[str, Any], task_tokens: frozenset[str]) -> int:
    if not task_tokens:
        return 0
    row_tokens = set(query_tokenizer.tokenize(f"{row.get('name') or ''} {row.get('path') or ''}"))
    return len(task_tokens & row_tokens)


def _load_rows(
    index: ProjectIndex,
    node_ids: Sequence[str],
) -> dict[str, dict[str, Any]]:
    unique = [node_id for node_id in dict.fromkeys(node_ids) if node_id.startswith("symbol::")]
    if not unique:
        return {}
    placeholders = ",".join("?" for _ in unique)
    rows = index.conn.execute(
        f"""
        SELECT id, name, path, start_line, end_line
        FROM entities
        WHERE id IN ({placeholders})
        """,
        tuple(unique),
    ).fetchall()
    return {str(row["id"]): dict(row) for row in rows}


def _line_range(row: dict[str, Any]) -> list[int] | None:
    start = row.get("start_line")
    end = row.get("end_line")
    if start is None or end is None:
        return None
    return [int(start), int(end)]


def _response_tokens(response: ContextResponse) -> int:
    return count_payload_tokens(to_dict(response), DEFAULT_ENCODING)


__all__ = [
    "IMPACT_CONTEXT_DEPTH",
    "IMPACT_CONTEXT_TOKEN_BUDGET",
    "build_impact_context",
]
