from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from csegraph_core.index.repository import ProjectIndex


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


def load_symbols(index: ProjectIndex) -> Dict[str, Dict[str, Any]]:
    rows = index.conn.execute(
        f"""
        SELECT id, parent_id, type AS kind, name, path AS file_path,
               language, signature, docstring, start_line, end_line, source_hash, metadata,
               parent_id AS parent_symbol_id
        FROM nodes
        WHERE type IN ({",".join("?" for _ in SYMBOL_TYPES)})
        """,
        SYMBOL_TYPES,
    )
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


def edge_maps(
    edges: Sequence[Dict[str, Any]],
) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, List[Dict[str, Any]]]]:
    outgoing: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    incoming: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        outgoing[edge["source_id"]].append(edge)
        incoming[edge["target_id"]].append(edge)
    return outgoing, incoming
