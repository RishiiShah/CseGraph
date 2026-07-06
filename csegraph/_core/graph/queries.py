from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from csegraph._core.core.models import (
    GraphEdgeView,
    GraphNodeView,
    GraphResult,
    PathEdge,
    PathResult,
    PathStep,
)
from csegraph._core.index.repository import ProjectIndex

VALID_CONFIDENCE_TIERS = frozenset({"EXTRACTED", "INFERRED", "AMBIGUOUS"})

# Graph and path are focused escalation tools. These limits keep an accidental
# high-degree target from turning either operation into a repository dump.
_MAX_GRAPH_DEPTH = 3
_MAX_GRAPH_NODES = 100
_MAX_GRAPH_EDGES = 200
_MAX_PATH_DEPTH = 15
_MAX_PATH_VISITED = 2_000
_MAX_FRONTIER_EDGES = 4_000


class GraphQueryService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def neighborhood(
        self,
        node_id: str,
        depth: int = 1,
        relations: Optional[List[str]] = None,
        confidence_tiers: Optional[List[str]] = None,
    ) -> GraphResult:
        if depth < 1 or depth > _MAX_GRAPH_DEPTH:
            raise ValueError(f"depth must be between 1 and {_MAX_GRAPH_DEPTH}")
        relation_filter = _clean_filter(relations)
        tier_filter = _confidence_filter(confidence_tiers)

        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            repo_root = index.metadata()["root_dir"]
            target = _resolve_graph_node(index, node_id, repo_root)

            visited: Set[str] = {target}
            frontier: List[str] = [target]
            truncated = False

            for _ in range(depth):
                if not frontier:
                    break
                rows = _adjacent_edges(
                    index,
                    frontier,
                    relation_filter,
                    tier_filter,
                    limit=_MAX_FRONTIER_EDGES,
                )
                if len(rows) == _MAX_FRONTIER_EDGES:
                    truncated = True

                next_frontier: List[str] = []
                for edge in rows:
                    source = str(edge["source"])
                    target_id = str(edge["target"])
                    for candidate in (source, target_id):
                        if candidate in visited:
                            continue
                        if len(visited) >= _MAX_GRAPH_NODES:
                            truncated = True
                            continue
                        visited.add(candidate)
                        next_frontier.append(candidate)
                frontier = next_frontier

            # Include all relationships among the selected entities, not only
            # the edges first encountered by breadth-first expansion.
            selected_edges = _edges_within(
                index,
                sorted(visited),
                relation_filter,
                tier_filter,
                limit=_MAX_GRAPH_EDGES + 1,
            )
            if len(selected_edges) > _MAX_GRAPH_EDGES:
                truncated = True
                selected_edges = selected_edges[:_MAX_GRAPH_EDGES]

            entity_rows = _entities_by_id(index, sorted(visited))
            nodes = [_node_view_from_row(entity_id, entity_rows) for entity_id in sorted(visited)]
            edges = [_graph_edge_from_row(edge) for edge in selected_edges]
            summary = (
                f"{len(nodes)} entities, {len(edges)} edges within depth {depth} around "
                f"'{_short_name(target, entity_rows)}'."
            )
            if truncated:
                summary += " Result truncated to focused-query limits."

            return GraphResult(
                target=target,
                depth=depth,
                nodes=nodes,
                edges=edges,
                summary=summary,
                total_nodes=len(nodes),
                total_edges=len(edges),
                truncated=truncated,
            )
        finally:
            index.close()

    def shortest_path(
        self,
        source: str,
        target: str,
        relations: Optional[List[str]] = None,
        confidence_tiers: Optional[List[str]] = None,
    ) -> PathResult:
        relation_filter = _clean_filter(relations)
        tier_filter = _confidence_filter(confidence_tiers)

        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            repo_root = index.metadata()["root_dir"]
            source_id = _resolve_graph_node(index, source, repo_root)
            target_id = _resolve_graph_node(index, target, repo_root)

            parent: Dict[str, Tuple[str, Dict[str, Any]]] = {}
            visited: Set[str] = {source_id}
            frontier: List[str] = [source_id]
            found = source_id == target_id

            for _ in range(_MAX_PATH_DEPTH):
                if found or not frontier or len(visited) >= _MAX_PATH_VISITED:
                    break
                rows = _adjacent_edges(
                    index,
                    frontier,
                    relation_filter,
                    tier_filter,
                    limit=_MAX_FRONTIER_EDGES,
                )
                next_frontier: List[str] = []
                frontier_set = set(frontier)
                for edge in rows:
                    source_entity = str(edge["source"])
                    target_entity = str(edge["target"])
                    for current, candidate in (
                        (source_entity, target_entity),
                        (target_entity, source_entity),
                    ):
                        if current not in frontier_set or candidate in visited:
                            continue
                        if len(visited) >= _MAX_PATH_VISITED:
                            break
                        visited.add(candidate)
                        parent[candidate] = (current, edge)
                        next_frontier.append(candidate)
                        if candidate == target_id:
                            found = True
                            break
                    if found:
                        break
                frontier = next_frontier

            if not found:
                return PathResult(
                    source=source_id,
                    target=target_id,
                    found=False,
                    length=0,
                    nodes=[],
                    edges=[],
                    summary=f"No path: '{source}' ↛ '{target}'.",
                )

            path_ids, path_edge_rows = _reconstruct_path(
                source_id,
                target_id,
                parent,
            )
            entity_rows = _entities_by_id(index, path_ids)
            nodes = [_path_step_from_row(entity_id, entity_rows) for entity_id in path_ids]
            edges = [
                PathEdge(
                    source=str(edge["source"]),
                    target=str(edge["target"]),
                    relation=str(edge["relation"]),
                )
                for edge in path_edge_rows
            ]
            name_chain = " → ".join(_short_name(entity_id, entity_rows) for entity_id in path_ids)

            return PathResult(
                source=source_id,
                target=target_id,
                found=True,
                length=len(edges),
                nodes=nodes,
                edges=edges,
                summary=f"{name_chain} ({len(edges)} hops)",
            )
        finally:
            index.close()


def _clean_filter(values: Optional[Sequence[str]]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in (values or ()) if value.strip()))


def _confidence_filter(values: Optional[Sequence[str]]) -> Tuple[str, ...]:
    tiers = _clean_filter(values)
    unknown = set(tiers) - VALID_CONFIDENCE_TIERS
    if unknown:
        raise ValueError(
            f"Unknown confidence_tiers: {sorted(unknown)}. Valid: {sorted(VALID_CONFIDENCE_TIERS)}"
        )
    return tiers


def _edge_filters(
    relations: Sequence[str],
    confidence_tiers: Sequence[str],
) -> Tuple[List[str], List[Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    if relations:
        clauses.append(f"relation IN ({','.join('?' for _ in relations)})")
        params.extend(relations)
    if confidence_tiers:
        clauses.append(f"confidence_tier IN ({','.join('?' for _ in confidence_tiers)})")
        params.extend(confidence_tiers)
    return clauses, params


def _adjacent_edges(
    index: ProjectIndex,
    entity_ids: Sequence[str],
    relations: Sequence[str],
    confidence_tiers: Sequence[str],
    *,
    limit: int,
) -> List[Dict[str, Any]]:
    if not entity_ids:
        return []
    placeholders = ",".join("?" for _ in entity_ids)
    filters, filter_params = _edge_filters(relations, confidence_tiers)
    where = [f"(source IN ({placeholders}) OR target IN ({placeholders}))", *filters]
    rows = index.conn.execute(
        f"""
        SELECT source, target, relation, confidence, confidence_tier
        FROM edges
        WHERE {" AND ".join(where)}
        ORDER BY source, relation, target
        LIMIT ?
        """,
        [*entity_ids, *entity_ids, *filter_params, limit],
    )
    return [dict(row) for row in rows]


def _edges_within(
    index: ProjectIndex,
    entity_ids: Sequence[str],
    relations: Sequence[str],
    confidence_tiers: Sequence[str],
    *,
    limit: int,
) -> List[Dict[str, Any]]:
    if not entity_ids:
        return []
    placeholders = ",".join("?" for _ in entity_ids)
    filters, filter_params = _edge_filters(relations, confidence_tiers)
    where = [
        f"source IN ({placeholders})",
        f"target IN ({placeholders})",
        *filters,
    ]
    rows = index.conn.execute(
        f"""
        SELECT source, target, relation, confidence, confidence_tier
        FROM edges
        WHERE {" AND ".join(where)}
        ORDER BY source, relation, target
        LIMIT ?
        """,
        [*entity_ids, *entity_ids, *filter_params, limit],
    )
    return [dict(row) for row in rows]


def _entities_by_id(
    index: ProjectIndex,
    entity_ids: Iterable[str],
) -> Dict[str, Dict[str, Any]]:
    ids = list(dict.fromkeys(entity_ids))
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = index.conn.execute(
        f"""
        SELECT id, type, name, path, start_line, end_line
        FROM entities
        WHERE id IN ({placeholders})
        """,
        ids,
    )
    return {str(row["id"]): dict(row) for row in rows}


def _resolve_graph_node(
    index: ProjectIndex,
    node: str,
    repo_root: str = "",
) -> str:
    candidate = node.strip()
    if not candidate:
        raise ValueError("Node must be a non-empty entity ID, file path, or symbol name.")

    row = index.conn.execute(
        "SELECT id FROM entities WHERE id = ?",
        (candidate,),
    ).fetchone()
    if row is not None:
        return str(row["id"])

    normalized_path = _repo_relative_path(candidate, repo_root)
    if normalized_path is not None:
        row = index.conn.execute(
            """
            SELECT id FROM files
            WHERE path = ? COLLATE NOCASE
            ORDER BY path, id
            LIMIT 1
            """,
            (normalized_path,),
        ).fetchone()
        if row is not None:
            return str(row["id"])

    row = index.conn.execute(
        """
        SELECT id FROM symbols
        WHERE name = ? COLLATE NOCASE
        ORDER BY length(name), start_line, id
        LIMIT 1
        """,
        (candidate,),
    ).fetchone()
    if row is not None:
        return str(row["id"])

    row = index.conn.execute(
        """
        SELECT id FROM files
        WHERE name = ? COLLATE NOCASE
        ORDER BY length(path), path, id
        LIMIT 1
        """,
        (candidate,),
    ).fetchone()
    if row is not None:
        return str(row["id"])

    raise ValueError(f"Node '{node}' did not match any indexed file or symbol.")


def _repo_relative_path(value: str, repo_root: str) -> Optional[str]:
    path = Path(value)
    if path.is_absolute():
        if not repo_root:
            return None
        try:
            return path.resolve().relative_to(Path(repo_root).resolve()).as_posix()
        except ValueError:
            return None
    normalized = path.as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _reconstruct_path(
    source: str,
    target: str,
    parent: Dict[str, Tuple[str, Dict[str, Any]]],
) -> Tuple[List[str], List[Dict[str, Any]]]:
    path_ids = [target]
    edges: List[Dict[str, Any]] = []
    current = target
    while current != source:
        previous, edge = parent[current]
        edges.append(edge)
        path_ids.append(previous)
        current = previous
    path_ids.reverse()
    edges.reverse()
    return path_ids, edges


def _graph_edge_from_row(row: Dict[str, Any]) -> GraphEdgeView:
    return GraphEdgeView(
        source=str(row["source"]),
        target=str(row["target"]),
        relation=str(row["relation"]),
        confidence=float(row["confidence"]),
        confidence_tier=str(row["confidence_tier"]),
    )


def _node_view_from_row(
    node_id: str,
    entity_rows: Dict[str, Dict[str, Any]],
) -> GraphNodeView:
    row = entity_rows.get(node_id)
    if row is None:
        return GraphNodeView(id=node_id, kind="external", name=node_id, path="")
    return GraphNodeView(
        id=node_id,
        kind=str(row["type"]),
        name=str(row["name"]),
        path=str(row["path"]),
        line_range=_line_range(row),
    )


def _path_step_from_row(
    node_id: str,
    entity_rows: Dict[str, Dict[str, Any]],
) -> PathStep:
    row = entity_rows.get(node_id)
    if row is None:
        return PathStep(node_id=node_id, kind="external", name=node_id, path="")
    return PathStep(
        node_id=node_id,
        kind=str(row["type"]),
        name=str(row["name"]),
        path=str(row["path"]),
        line_range=_line_range(row),
    )


def _line_range(row: Dict[str, Any]) -> Optional[List[int]]:
    start = row.get("start_line")
    end = row.get("end_line")
    if start is None or end is None:
        return None
    return [int(start), int(end)]


def _short_name(node_id: str, entity_rows: Dict[str, Dict[str, Any]]) -> str:
    row = entity_rows.get(node_id)
    return str(row["name"]) if row is not None else node_id
