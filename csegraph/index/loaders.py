from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Sequence, Tuple

from csegraph.index.repository import ProjectIndex
from csegraph.core.ids import file_node_id


def load_files(index: ProjectIndex, project_id: int) -> Dict[str, Dict[str, Any]]:
    files: Dict[str, Dict[str, Any]] = {}
    for row in index.conn.execute(
        "SELECT * FROM files WHERE project_id = ?",
        (project_id,),
    ):
        files[file_node_id(row["path"])] = dict(row)
    return files


def load_symbols(index: ProjectIndex, project_id: int) -> Dict[str, Dict[str, Any]]:
    symbols: Dict[str, Dict[str, Any]] = {}
    for row in index.conn.execute(
        """
        SELECT s.*, f.path AS file_path
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE s.project_id = ?
        """,
        (project_id,),
    ):
        symbols[row["id"]] = dict(row)
    return symbols


def load_summaries(index: ProjectIndex, project_id: int) -> Dict[str, str]:
    return {
        row["node_id"]: row["summary"]
        for row in index.conn.execute(
            "SELECT node_id, summary FROM summaries WHERE project_id = ?",
            (project_id,),
        )
    }


def load_edges(index: ProjectIndex, project_id: int) -> List[Dict[str, Any]]:
    return [
        dict(row)
        for row in index.conn.execute(
            "SELECT * FROM edges WHERE project_id = ?",
            (project_id,),
        )
    ]


def edge_maps(
    edges: Sequence[Dict[str, Any]],
) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, List[Dict[str, Any]]]]:
    outgoing: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    incoming: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        outgoing[edge["source_id"]].append(edge)
        incoming[edge["target_id"]].append(edge)
    return outgoing, incoming
