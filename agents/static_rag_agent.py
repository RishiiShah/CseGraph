"""Static RAG Agent — static_rag_agent.py

Baseline: Context is selected purely by BM25 relevance to the query.
The top-K highest-scoring nodes are taken as-is — no adaptive expansion,
no dependency tier logic, no confidence fallback.

Produces a SufficiencyResult with:
  - context_node_ids = top-K nodes by BM25 (compressed summaries used)
  - raw_code_nodes   = []   (no raw-code fallback)
  - is_sufficient    = True (no gating — always proceeds)
"""

from __future__ import annotations

import json
import math
import os
import sys
from collections import defaultdict
from typing import Dict, List, Tuple

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.cse_agent import CSEAgent
from models.cse_result import SufficiencyQuery, SufficiencyResult


class StaticRAGAgent:
    """Baseline: static top-K retrieval by BM25 similarity, no expansion."""

    DEFAULT_TOP_K: int = 20

    def __init__(
        self,
        link_graph_path: str,
        compressed_graph_path: str,
        top_k: int = DEFAULT_TOP_K,
    ) -> None:
        self._cse = CSEAgent(link_graph_path, compressed_graph_path)
        self.top_k = top_k

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_context(self, query: SufficiencyQuery) -> SufficiencyResult:
        """Return a SufficiencyResult with the top-K BM25-relevant nodes.

        BM25 IDF is computed over the full node-summary corpus so scores
        reflect genuine relevance rather than single-document frequency.
        """
        ranked = self._rank_nodes_by_bm25(query.query_text)
        top_k_ids = [nid for nid, _ in ranked[: self.top_k]]

        metrics = self._cse._compute_all_metrics(
            query,
            context_ids=top_k_ids,
            raw_code_ids=set(),
        )

        return SufficiencyResult(
            is_sufficient=True,
            metrics=metrics,
            context_node_ids=sorted(top_k_ids),
            raw_code_nodes=[],
            expansion_rounds=0,
            max_rounds=0,
            thresholds={},
            reason=f"Static RAG baseline: top-{self.top_k} nodes by BM25",
            query=query,
        )

    def save_result(self, result: SufficiencyResult, output_path: str) -> None:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result.model_dump(), f, indent=4)
        print(f"Saved static-RAG result to '{output_path}'")

    # ------------------------------------------------------------------
    # BM25 ranking over the full node-summary corpus
    # ------------------------------------------------------------------

    def _rank_nodes_by_bm25(self, query_text: str) -> List[Tuple[str, float]]:
        """Rank all nodes in the compressed graph by BM25 score against the query.

        IDF is computed over the entire summary corpus so that common
        template terms (e.g. 'function', 'method') are down-weighted
        correctly.
        """
        query_tokens = self._cse._code_tokenize(query_text)
        if not query_tokens:
            return []

        cg = self._cse.compressed_graph
        node_ids = list(cg.node_summaries.keys())
        doc_token_lists: List[List[str]] = [
            self._cse._code_tokenize(cg.node_summaries[nid].summary)
            for nid in node_ids
        ]

        N = len(doc_token_lists)
        if N == 0:
            return []

        avg_dl = sum(len(d) for d in doc_token_lists) / N

        # Corpus-level document frequency
        df: Dict[str, int] = defaultdict(int)
        for doc in doc_token_lists:
            for t in set(doc):
                df[t] += 1

        k1, b = 1.5, 0.75

        scores: List[Tuple[str, float]] = []
        for nid, doc in zip(node_ids, doc_token_lists):
            dl = len(doc)
            tf: Dict[str, float] = defaultdict(float)
            for t in doc:
                tf[t] += 1.0

            score = 0.0
            for qt in set(query_tokens):
                n_t = df.get(qt, 0)
                idf = math.log((N - n_t + 0.5) / (n_t + 0.5) + 1.0)
                tf_d = tf.get(qt, 0.0)
                tf_bm25 = (tf_d * (k1 + 1.0)) / (
                    tf_d + k1 * (1.0 - b + b * dl / max(avg_dl, 1.0))
                )
                score += idf * tf_bm25

            scores.append((nid, score))

        return sorted(scores, key=lambda x: x[1], reverse=True)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Run the Static RAG baseline agent."
    )
    parser.add_argument("--link-graph", default="data/link_graph.json")
    parser.add_argument("--compressed-graph", default="data/compressed_graph.json")
    parser.add_argument("--top-k", type=int, default=StaticRAGAgent.DEFAULT_TOP_K)
    parser.add_argument("--output", default="data/static_rag_result.json")
    args = parser.parse_args()

    agent = StaticRAGAgent(args.link_graph, args.compressed_graph, top_k=args.top_k)
    target_id, target_file, auto_query = agent._cse.pick_representative_target()

    from models.cse_result import SufficiencyQuery
    query = SufficiencyQuery(
        query_text=auto_query,
        target_node_id=target_id,
        target_file_path=target_file,
    )

    result = agent.build_context(query)
    agent.save_result(result, args.output)

    print(f"Target            : {target_id}")
    print(f"Context nodes     : {len(result.context_node_ids)}")
    print(f"Dep. completeness : {result.metrics.dependency_completeness:.2%}")
    print(f"Entity coverage   : {result.metrics.entity_coverage:.2%}")
    print(f"Semantic overlap  : {result.metrics.semantic_overlap:.2%}")
    print(f"Model confidence  : {result.metrics.model_confidence:.2%}")
    print(f"Reason            : {result.reason}")
