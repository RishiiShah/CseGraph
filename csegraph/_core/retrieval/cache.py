from __future__ import annotations

import copy
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List

from csegraph._core.index.loaders import load_edge_maps, load_summaries, load_symbols
from csegraph._core.index.repository import ProjectIndex

logger = logging.getLogger("csegraph.cache")

@dataclass
class GraphSnapshot:
    """Immutable snapshot of the repository topology."""
    db_path: str
    data_version: int
    files: Dict[str, Dict[str, Any]]
    symbols_light: Dict[str, Dict[str, Any]]
    node_rows_light: Dict[str, Dict[str, Any]]
    summaries: Dict[str, str]
    outgoing: Dict[str, List[Dict[str, Any]]]
    incoming: Dict[str, List[Dict[str, Any]]]

    def get_files(self) -> Dict[str, Dict[str, Any]]:
        # deepcopy prevents shared mutation pollution
        return copy.deepcopy(self.files)

    def get_symbols_light(self) -> Dict[str, Dict[str, Any]]:
        return copy.deepcopy(self.symbols_light)

    def get_node_rows_light(self) -> Dict[str, Dict[str, Any]]:
        return copy.deepcopy(self.node_rows_light)

    def get_summaries(self) -> Dict[str, str]:
        return self.summaries.copy()

    def get_edge_maps(self) -> tuple[Dict[str, List[Dict[str, Any]]], Dict[str, List[Dict[str, Any]]]]:
        return copy.deepcopy(self.outgoing), copy.deepcopy(self.incoming)


class SnapshotManager:
    """Bounded process-local cache for GraphSnapshots."""
    def __init__(self, max_size: int = 5):
        self._max_size = max_size
        self._snapshots: Dict[str, GraphSnapshot] = {}

    def get_snapshot(self, index: ProjectIndex) -> GraphSnapshot:
        db_path = index.db_path
        
        fingerprint = 0
        for p in (db_path, f"{db_path}-wal", f"{db_path}-shm"):
            try:
                st = os.stat(p)
                fingerprint ^= st.st_mtime_ns ^ st.st_size
            except OSError:
                pass
        current_data_version = fingerprint

        snapshot = self._snapshots.get(db_path)
        if snapshot is not None and snapshot.data_version == current_data_version:
            return snapshot

        # Cache miss or invalidation
        logger.debug(f"Loading GraphSnapshot for {db_path} (data_version: {current_data_version})")
        
        # Load lightweight properties of ALL nodes (for files and basic lookups)
        nodes_rows = index.conn.execute(
            "SELECT id, parent_id, type, type AS kind, name, path, path AS file_path, language, start_line, end_line, source_hash FROM nodes"
        ).fetchall()
        node_rows_light = {row["id"]: dict(row) for row in nodes_rows}
        
        files = {k: v for k, v in node_rows_light.items() if v["kind"] == "file"}
        symbols_light = load_symbols(index, exclude_heavy=True)

        summaries = load_summaries(index)
        outgoing, incoming = load_edge_maps(index)

        snapshot = GraphSnapshot(
            db_path=db_path,
            data_version=current_data_version,
            files=files,
            symbols_light=symbols_light,
            node_rows_light=node_rows_light,
            summaries=summaries,
            outgoing=outgoing,
            incoming=incoming,
        )

        if len(self._snapshots) >= self._max_size and db_path not in self._snapshots:
            # Simple eviction: remove a random/first item
            evict_key = next(iter(self._snapshots.keys()))
            del self._snapshots[evict_key]

        self._snapshots[db_path] = snapshot
        return snapshot

    def clear(self, db_path: str | None = None) -> None:
        if db_path:
            self._snapshots.pop(db_path, None)
        else:
            self._snapshots.clear()

# Global process-local cache instance
CACHE = SnapshotManager()
