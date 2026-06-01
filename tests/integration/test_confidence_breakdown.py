"""Confidence-tier breakdown on GraphResult.

Surfaces the count of edges by confidence_tier (EXTRACTED / INFERRED / AMBIGUOUS)
in both minimal and standard responses, so agents can triage by trust without
needing per-edge metadata.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from csegraph_core.graph.queries import (
    GraphQueryService,
    _confidence_breakdown,
    _confidence_note,
)
from csegraph_core.index.services import IndexService


def _tiny_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "from helpers import fmt\n"
        "\n"
        "def greet(name: str) -> str:\n"
        "    return fmt(name)\n",
        encoding="utf-8",
    )
    (repo / "helpers.py").write_text(
        "def fmt(name: str) -> str:\n"
        "    return f\"hi {name}\"\n",
        encoding="utf-8",
    )
    db = str(tmp_path / "test.db")
    IndexService(db).index(repo, profile="small")
    return repo, db


def _inject_mixed_tier_edges(db: str, source: str, target_prefix: str, tier: str) -> None:
    """Insert 3 edges with the given confidence_tier originating from `source`."""
    conn = sqlite3.connect(db)
    try:
        for i in range(3):
            nid = f"{target_prefix}{i}"
            conn.execute(
                """
                INSERT OR IGNORE INTO nodes
                  (id, type, name, path, language, source_hash, updated_at)
                VALUES (?, 'function', ?, 'synthetic.py', 'python', 'synthetic', 0)
                """,
                (nid, nid),
            )
            conn.execute(
                "INSERT OR IGNORE INTO edges "
                "(source, target, relation, confidence, confidence_tier) "
                "VALUES (?, ?, 'calls', 1.0, ?)",
                (source, nid, tier),
            )
        conn.commit()
    finally:
        conn.close()


def _inject_node_and_edge(db: str, source: str, target: str, tier: str) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO nodes
              (id, type, name, path, language, source_hash, updated_at)
            VALUES (?, 'function', ?, 'synthetic.py', 'python', 'synthetic', 0)
            """,
            (target, target.rsplit("::", 1)[-1]),
        )
        conn.execute(
            "INSERT OR IGNORE INTO edges "
            "(source, target, relation, confidence, confidence_tier) "
            "VALUES (?, ?, 'calls', 1.0, ?)",
            (source, target, tier),
        )
        conn.commit()
    finally:
        conn.close()


class TestBreakdownHelpers:
    def test_empty_returns_empty_dict(self):
        assert _confidence_breakdown([]) == {}

    def test_counts_tiers_correctly(self):
        edges = [
            {"confidence_tier": "EXTRACTED"},
            {"confidence_tier": "EXTRACTED"},
            {"confidence_tier": "INFERRED"},
            {"confidence_tier": None},  # missing tier defaults to EXTRACTED
        ]
        assert _confidence_breakdown(edges) == {"EXTRACTED": 3, "INFERRED": 1}

    def test_note_silent_when_only_extracted(self):
        assert _confidence_note({"EXTRACTED": 10}) == ""

    def test_note_mentions_non_extracted(self):
        note = _confidence_note({"EXTRACTED": 10, "INFERRED": 3, "AMBIGUOUS": 1})
        assert "3 inferred" in note
        assert "1 ambiguous" in note
        assert "extracted" not in note.lower()


class TestBreakdownOnNeighborhood:
    def test_minimal_includes_breakdown(self, tmp_path):
        _, db = _tiny_repo(tmp_path)
        result = GraphQueryService(db).neighborhood(
            "file::app.py", depth=2, detail_level="minimal"
        )
        assert result.confidence_breakdown
        assert result.confidence_breakdown.get("EXTRACTED", 0) >= 1

    def test_standard_includes_breakdown(self, tmp_path):
        _, db = _tiny_repo(tmp_path)
        result = GraphQueryService(db).neighborhood(
            "file::app.py", depth=2, detail_level="standard"
        )
        # All real-csegraph edges today are EXTRACTED.
        assert result.confidence_breakdown == {"EXTRACTED": result.total_edges}

    def test_summary_silent_when_all_extracted(self, tmp_path):
        _, db = _tiny_repo(tmp_path)
        result = GraphQueryService(db).neighborhood(
            "file::app.py", depth=2, detail_level="standard"
        )
        assert "inferred" not in result.summary
        assert "ambiguous" not in result.summary

    def test_summary_mentions_inferred_when_present(self, tmp_path):
        repo, db = _tiny_repo(tmp_path)
        _inject_mixed_tier_edges(
            db,
            source="symbol::app.py::function::greet",
            target_prefix="symbol::synthetic.py::function::inferred_",
            tier="INFERRED",
        )
        result = GraphQueryService(db).neighborhood(
            "greet", depth=1, detail_level="standard"
        )
        assert result.confidence_breakdown.get("INFERRED", 0) >= 3
        assert "inferred" in result.summary

    def test_breakdown_respects_relations_filter(self, tmp_path):
        repo, db = _tiny_repo(tmp_path)
        # Add 3 INFERRED `calls` edges from greet.
        _inject_mixed_tier_edges(
            db,
            source="symbol::app.py::function::greet",
            target_prefix="symbol::synthetic.py::function::inf_call_",
            tier="INFERRED",
        )
        # Filter to imports only — none of the inferred edges should appear.
        result = GraphQueryService(db).neighborhood(
            "greet",
            depth=1,
            detail_level="standard",
            relations=["imports"],
        )
        assert result.confidence_breakdown.get("INFERRED", 0) == 0

    def test_confidence_tier_filter_excludes_materialized_edges(self, tmp_path):
        _, db = _tiny_repo(tmp_path)
        greet = "symbol::app.py::function::greet"
        left = "symbol::synthetic.py::function::left"
        right = "symbol::synthetic.py::function::right"
        _inject_node_and_edge(db, greet, left, "EXTRACTED")
        _inject_node_and_edge(db, greet, right, "EXTRACTED")
        _inject_node_and_edge(db, left, right, "INFERRED")

        result = GraphQueryService(db).neighborhood(
            "greet",
            depth=1,
            detail_level="standard",
            confidence_tiers=["EXTRACTED"],
        )

        assert result.confidence_breakdown.get("INFERRED", 0) == 0
        assert {edge.confidence_tier for edge in result.edges} == {"EXTRACTED"}

    def test_path_confidence_tier_filter_excludes_inferred_edges(self, tmp_path):
        _, db = _tiny_repo(tmp_path)
        greet = "symbol::app.py::function::greet"
        target = "symbol::synthetic.py::function::target"
        other = "symbol::synthetic.py::function::other"
        _inject_node_and_edge(db, greet, target, "EXTRACTED")
        _inject_node_and_edge(db, greet, other, "INFERRED")
        _inject_node_and_edge(db, other, target, "INFERRED")

        result = GraphQueryService(db).shortest_path(
            "greet",
            "target",
            detail_level="standard",
            confidence_tiers=["EXTRACTED"],
        )

        assert result.found is True
        assert result.confidence_breakdown == {"EXTRACTED": result.length}
