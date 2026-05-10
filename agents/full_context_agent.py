from __future__ import annotations

import json
import os
import sys
from typing import TYPE_CHECKING, List, Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.cse_agent import CSEAgent
from models.cse_result import SufficiencyMetrics, SufficiencyQuery, SufficiencyResult

if TYPE_CHECKING:
    from csegraph_core.core.models import ProfileConfig


class FullContextAgent:
    """Baseline: all repository symbols included as raw verbatim source."""

    def __init__(
        self,
        link_graph_path: str,
        compressed_graph_path: str,
        profile: Optional["ProfileConfig"] = None,
    ) -> None:
        self._cse = CSEAgent(link_graph_path, compressed_graph_path, profile=profile)

    # Public API
    def build_context(self, query: SufficiencyQuery) -> SufficiencyResult:
        all_symbol_ids = self._all_symbol_ids()

        # Metrics: compute over all symbols treated as raw-code nodes.
        # _compute_all_metrics reads verbatim source for nodes in raw_code_ids.
        metrics = self._cse._compute_all_metrics(
            query,
            context_ids=all_symbol_ids,
            raw_code_ids=set(all_symbol_ids),
        )

        return SufficiencyResult(
            is_sufficient=True,
            metrics=metrics,
            context_node_ids=[],
            raw_code_nodes=sorted(all_symbol_ids),
            expansion_rounds=0,
            max_rounds=0,
            thresholds={},
            reason=f"Full context baseline: {len(all_symbol_ids)} symbols as raw source",
            query=query,
        )

    def save_result(self, result: SufficiencyResult, output_path: str) -> None:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result.model_dump(), f, indent=4)
        print(f"Saved full-context result to '{output_path}'")

    # Helpers
    def _all_symbol_ids(self) -> List[str]:
        """Return IDs of every non-file node in the link graph."""
        return [
            node.id
            for node in self._cse.link_graph.nodes
            if node.type != "file"
        ]


# CLI entry point
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Run the Full Context baseline agent."
    )
    parser.add_argument("--link-graph", default="data/link_graph.json")
    parser.add_argument("--compressed-graph", default="data/compressed_graph.json")
    parser.add_argument("--output", default="data/full_context_result.json")
    args = parser.parse_args()

    agent = FullContextAgent(args.link_graph, args.compressed_graph)
    target_id, target_file, auto_query = agent._cse.pick_representative_target()

    from models.cse_result import SufficiencyQuery
    query = SufficiencyQuery(
        query_text=auto_query,
        target_node_id=target_id,
        target_file_path=target_file,
    )

    result = agent.build_context(query)
    agent.save_result(result, args.output)

    print(f"Target: {target_id}")
    print(f"Raw code nodes: {len(result.raw_code_nodes)}")
    print(f"Dep. completeness: {result.metrics.dependency_completeness:.2%}")
    print(f"Entity coverage: {result.metrics.entity_coverage:.2%}")
    print(f"Semantic overlap: {result.metrics.semantic_overlap:.2%}")
    print(f"Model confidence: {result.metrics.model_confidence:.2%}")
    print(f"Reason: {result.reason}")
