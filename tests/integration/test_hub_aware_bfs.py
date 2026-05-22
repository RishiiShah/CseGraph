"""Hub-aware BFS in GraphQueryService.neighborhood().

Verifies that very-high-degree nodes (degree > p99, floor 50) are not expanded
through during BFS, unless the hub is the resolved target itself.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from csegraph_core.graph.queries import (
    GraphQueryService,
    _HUB_FLOOR,
    _compute_hub_threshold,
    _hub_node_ids,
)
from csegraph_core.index.repository import ProjectIndex
from csegraph_core.index.services import IndexService


def _tiny_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        'from helpers import fmt\n\ndef greet(name: str) -> str:\n    return fmt(name)\n',
        encoding="utf-8",
    )
    (repo / "helpers.py").write_text(
        'def fmt(name: str) -> str:\n    return f"hi {name}"\n',
        encoding="utf-8",
    )
    db = str(tmp_path / "test.db")
    IndexService(db).index(repo, profile="small")
    return repo, db


def _inject_hub_edges(db: str, hub_id: str, neighbor_prefix: str, count: int) -> None:
    """Insert `count` synthetic nodes and edges so `hub_id` has degree >= count."""
    conn = sqlite3.connect(db)
    try:
        for i in range(count):
            nid = f"{neighbor_prefix}{i}"
            conn.execute(
                """
                INSERT OR IGNORE INTO nodes
                  (id, type, name, path, language, source_hash, updated_at)
                VALUES (?, 'function', ?, 'synthetic.py', 'python', 'synthetic', 0)
                """,
                (nid, nid),
            )
            conn.execute(
                "INSERT OR IGNORE INTO edges (source, target, relation, confidence, confidence_tier) "
                "VALUES (?, ?, 'calls', 1.0, 'EXTRACTED')",
                (nid, hub_id),
            )
        conn.commit()
    finally:
        conn.close()


class TestHubThresholdHelpers:
    def test_threshold_uses_floor_on_tiny_graph(self, tmp_path):
        _, db = _tiny_repo(tmp_path)
        index = ProjectIndex(db)
        try:
            index.initialize_schema()
            assert _compute_hub_threshold(index) == _HUB_FLOOR
        finally:
            index.close()

    def test_no_hubs_in_tiny_graph(self, tmp_path):
        _, db = _tiny_repo(tmp_path)
        index = ProjectIndex(db)
        try:
            index.initialize_schema()
            threshold = _compute_hub_threshold(index)
            hubs = _hub_node_ids(index, threshold)
            assert hubs == set()
        finally:
            index.close()

    def test_threshold_rises_above_floor_when_a_node_is_a_hub(self, tmp_path):
        _, db = _tiny_repo(tmp_path)
        # Force `symbol::helpers.py::function::fmt` to have many incoming edges.
        _inject_hub_edges(
            db,
            hub_id="symbol::helpers.py::function::fmt",
            neighbor_prefix="symbol::synthetic.py::function::caller_",
            count=60,
        )
        index = ProjectIndex(db)
        try:
            index.initialize_schema()
            hubs = _hub_node_ids(index, _HUB_FLOOR)
            assert "symbol::helpers.py::function::fmt" in hubs
        finally:
            index.close()


class TestHubAwareNeighborhood:
    def test_tiny_graph_unaffected(self, tmp_path):
        repo, db = _tiny_repo(tmp_path)
        result = GraphQueryService(db).neighborhood(
            "greet", depth=1, detail_level="standard"
        )
        assert result.hubs_skipped == 0
        assert "Skipped" not in result.summary

    def test_expansion_suppressed_through_hub(self, tmp_path):
        repo, db = _tiny_repo(tmp_path)
        hub_id = "symbol::helpers.py::function::fmt"
        _inject_hub_edges(
            db,
            hub_id=hub_id,
            neighbor_prefix="symbol::synthetic.py::function::caller_",
            count=60,
        )

        # depth=2 from `greet` would normally reach all 60 synthetic callers via fmt.
        # Hub-aware BFS should NOT expand through fmt -> caller_*.
        result = GraphQueryService(db).neighborhood(
            "greet", depth=2, detail_level="standard"
        )
        synthetic_in_visited = [n for n in result.nodes if n.path == "synthetic.py"]
        assert synthetic_in_visited == []
        assert result.hubs_skipped >= 1
        assert "Skipped" in result.summary

    def test_target_is_hub_still_expands(self, tmp_path):
        repo, db = _tiny_repo(tmp_path)
        hub_id = "symbol::helpers.py::function::fmt"
        _inject_hub_edges(
            db,
            hub_id=hub_id,
            neighbor_prefix="symbol::synthetic.py::function::caller_",
            count=60,
        )

        # When fmt itself is the resolved target, expansion FROM fmt must still
        # happen so the agent can see its 1-hop neighbors.
        result = GraphQueryService(db).neighborhood(
            "fmt", depth=1, detail_level="standard"
        )
        synthetic_in_visited = [n for n in result.nodes if n.path == "synthetic.py"]
        assert len(synthetic_in_visited) >= 60
        # The hub-of-others is removed from the hub set before BFS for the target case.
        # hubs_skipped only counts OTHER hubs in the visited set; with just one hub
        # (now the target), hubs_skipped should be 0.
        assert result.hubs_skipped == 0
