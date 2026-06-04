from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from csegraph._core.index.repository import ProjectIndex


SYMBOL_TYPES = ("class", "function", "method", "test")


def load_nodes(
    index: ProjectIndex,
    types: Optional[Iterable[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    if types is None:
        rows = index.conn.execute(
            "SELECT * FROM nodes",
        )
    else:
        types = tuple(types)
        placeholders = ",".join("?" for _ in types)
        rows = index.conn.execute(
            f"SELECT * FROM nodes WHERE type IN ({placeholders})",
            types,
        )
    return {row["id"]: dict(row) for row in rows}


def load_files(index: ProjectIndex) -> Dict[str, Dict[str, Any]]:
    return load_nodes(index, types=("file",))


def load_symbols(
    index: ProjectIndex,
    ids: Optional[Iterable[str]] = None,
    exclude_heavy: bool = False,
) -> Dict[str, Dict[str, Any]]:
    columns = [
        "id", "parent_id", "type AS kind", "name", "path AS file_path",
        "language", "start_line", "end_line", "source_hash",
        "parent_id AS parent_symbol_id"
    ]
    if not exclude_heavy:
        columns.extend(["signature", "docstring", "metadata"])

    query = f"""
        SELECT {", ".join(columns)}
        FROM nodes
        WHERE type IN ({",".join("?" for _ in SYMBOL_TYPES)})
    """
    params = list(SYMBOL_TYPES)

    if ids is not None:
        ids_list = list(ids)
        if not ids_list:
            return {}
        placeholders = ",".join("?" for _ in ids_list)
        query += f" AND id IN ({placeholders})"
        params.extend(ids_list)

    rows = index.conn.execute(query, params)
    return {row["id"]: dict(row) for row in rows}


def load_summaries(index: ProjectIndex) -> Dict[str, str]:
    return {
        row["node_id"]: row["summary"]
        for row in index.conn.execute(
            "SELECT node_id, summary FROM summaries",
        )
    }


def load_edges(index: ProjectIndex) -> List[Dict[str, Any]]:
    rows = []
    for row in index.conn.execute(
        "SELECT * FROM edges",
    ):
        edge = dict(row)
        edge["source_id"] = edge["source"]
        edge["target_id"] = edge["target"]
        rows.append(edge)
    return rows


def load_edge_maps(
    index: ProjectIndex,
) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, List[Dict[str, Any]]]]:
    outgoing: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    incoming: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in index.conn.execute("SELECT * FROM edges"):
        edge = dict(row)
        edge["source_id"] = edge["source"]
        edge["target_id"] = edge["target"]
        outgoing[edge["source"]].append(edge)
        incoming[edge["target"]].append(edge)
    return outgoing, incoming
