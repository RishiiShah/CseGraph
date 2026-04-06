import json
import os
import re
import sys
from collections import defaultdict
from typing import Dict, List, Set, Tuple

# Add parent directory so running from agents/ works without PYTHONPATH.
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.compressed_graph import CompressedGraph
from models.cse_result import SufficiencyMetrics, SufficiencyQuery, SufficiencyResult
from models.link_graph import GraphEdge, GraphNode, LinkGraph


class CSEAgent:
    """Context Sufficiency Estimator.

    Evaluates whether retrieved context is sufficient for code generation
    using three metrics: dependency completeness, entity coverage, and
    semantic overlap.  When context is insufficient the agent expands it
    by increasing the BFS radius and pulling missing dependency nodes.
    """

    DEP_THRESHOLD = 0.80
    ENTITY_THRESHOLD = 0.80
    SEMANTIC_THRESHOLD = 0.50
    MAX_ROUNDS = 3

    def __init__(self, link_graph_path: str, compressed_graph_path: str):
        self.link_graph = self._load_link_graph(link_graph_path)
        self.compressed_graph = self._load_compressed_graph(compressed_graph_path)

        # Build adjacency indices from the link graph
        self._outgoing: Dict[str, List[GraphEdge]] = defaultdict(list)
        self._incoming: Dict[str, List[GraphEdge]] = defaultdict(list)
        self._node_lookup: Dict[str, GraphNode] = {}

        for node in self.link_graph.nodes:
            self._node_lookup[node.id] = node

        for edge in self.link_graph.edges:
            self._outgoing[edge.source].append(edge)
            self._incoming[edge.target].append(edge)

    # ------------------------------------------------------------------
    # Loading helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_link_graph(path: str) -> LinkGraph:
        with open(path, "r", encoding="utf-8") as f:
            return LinkGraph(**json.load(f))

    @staticmethod
    def _load_compressed_graph(path: str) -> CompressedGraph:
        with open(path, "r", encoding="utf-8") as f:
            return CompressedGraph(**json.load(f))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(self, query: SufficiencyQuery) -> SufficiencyResult:
        """Run the CSE evaluation loop.

        1. Retrieve initial context (radius-1 neighbourhood from compressed graph).
        2. Compute the three metrics.
        3. If any metric is below its threshold, expand and re-evaluate.
        4. Repeat up to ``MAX_ROUNDS`` times.
        """
        current_radius = 1
        context_ids = self._get_initial_context(query.target_node_id, current_radius)

        for round_num in range(self.MAX_ROUNDS):
            metrics = self._compute_all_metrics(query, context_ids)

            if self._all_pass(metrics):
                return self._build_result(
                    is_sufficient=True,
                    metrics=metrics,
                    context_ids=context_ids,
                    rounds=round_num,
                    reason="All thresholds met",
                    query=query,
                )

            # Expand context for next round
            current_radius += 1
            context_ids = self._expand_context(
                context_ids, query.target_node_id, current_radius
            )

        # Final evaluation after last expansion
        metrics = self._compute_all_metrics(query, context_ids)
        return self._build_result(
            is_sufficient=self._all_pass(metrics),
            metrics=metrics,
            context_ids=context_ids,
            rounds=self.MAX_ROUNDS,
            reason=(
                "All thresholds met"
                if self._all_pass(metrics)
                else "Max expansion rounds reached"
            ),
            query=query,
        )

    def pick_representative_target(self) -> Tuple[str, str]:
        """Auto-pick the highest-degree non-file node for demo purposes.

        Returns ``(node_id, file_path)``.
        """
        best_id = ""
        best_degree = -1
        for node_id in self._node_lookup:
            node = self._node_lookup[node_id]
            if node.type == "file":
                continue
            degree = len(self._outgoing.get(node_id, [])) + len(
                self._incoming.get(node_id, [])
            )
            if degree > best_degree:
                best_degree = degree
                best_id = node_id
        if not best_id:
            # Fallback to first node if no non-file nodes exist
            best_id = self.link_graph.nodes[0].id if self.link_graph.nodes else ""
        file_path = self._node_lookup[best_id].file_path if best_id else ""
        return best_id, file_path

    def save_result(self, result: SufficiencyResult, output_path: str) -> None:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result.model_dump(), f, indent=4)
        print(f"Saved CSE result to '{output_path}'")

    # ------------------------------------------------------------------
    # Context retrieval
    # ------------------------------------------------------------------

    def _get_initial_context(self, target_node_id: str, radius: int) -> List[str]:
        """Get context node IDs from the compressed graph's pre-computed slices.

        Falls back to BFS on the link graph if no matching slice exists.
        """
        slice_key = f"{target_node_id}@r{radius}"
        if slice_key in self.compressed_graph.context_slices:
            return list(
                self.compressed_graph.context_slices[slice_key].included_nodes.keys()
            )
        # Fallback: BFS on the link graph
        return self._bfs(target_node_id, radius)

    def _bfs(self, start_id: str, radius: int, max_nodes: int = 50) -> List[str]:
        """Breadth-first search on the link graph."""
        visited: Set[str] = {start_id}
        current_layer: Set[str] = {start_id}

        for _ in range(radius):
            if len(visited) >= max_nodes:
                break
            next_layer: Set[str] = set()
            for node_id in current_layer:
                for edge in self._outgoing.get(node_id, []):
                    if edge.target not in visited and len(visited) < max_nodes:
                        visited.add(edge.target)
                        next_layer.add(edge.target)
                for edge in self._incoming.get(node_id, []):
                    if edge.source not in visited and len(visited) < max_nodes:
                        visited.add(edge.source)
                        next_layer.add(edge.source)
            current_layer = next_layer
        return list(visited)

    # ------------------------------------------------------------------
    # Metric computation
    # ------------------------------------------------------------------

    def _compute_all_metrics(
        self, query: SufficiencyQuery, context_ids: List[str]
    ) -> SufficiencyMetrics:
        context_set = set(context_ids)
        context_names = {
            self._node_lookup[nid].name
            for nid in context_ids
            if nid in self._node_lookup
        }
        context_summaries = self._collect_summaries(context_ids)

        dep = self._compute_dependency_completeness(
            query.target_node_id, context_set
        )
        ent = self._compute_entity_coverage(query.query_text, context_names)
        sem = self._compute_semantic_overlap(query.query_text, context_summaries)

        return SufficiencyMetrics(
            dependency_completeness=dep,
            entity_coverage=ent,
            semantic_overlap=sem,
        )

    def _compute_dependency_completeness(
        self, target_id: str, context_ids: Set[str]
    ) -> float:
        """Fraction of target's outgoing call/import targets present in context."""
        deps: Set[str] = set()
        for edge in self._outgoing.get(target_id, []):
            if edge.relation in ("calls", "imports"):
                deps.add(edge.target)

        if not deps:
            return 1.0  # No dependencies → trivially complete

        resolved = deps & context_ids
        return len(resolved) / len(deps)

    def _compute_entity_coverage(
        self, query_text: str, context_node_names: Set[str]
    ) -> float:
        """Fraction of entities extracted from the query that appear in context."""
        entities = self._extract_query_entities(query_text)
        if not entities:
            return 1.0  # No entities to resolve → trivially covered

        # Case-insensitive comparison
        lower_names = {n.lower() for n in context_node_names}
        found = sum(1 for e in entities if e.lower() in lower_names)
        return found / len(entities)

    def _compute_semantic_overlap(
        self, query_text: str, context_summaries: List[str]
    ) -> float:
        """Cosine similarity between query and aggregated context using TF-IDF."""
        if not context_summaries:
            return 0.0

        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.metrics.pairwise import cosine_similarity
        except ImportError:
            # If sklearn is not installed, return a neutral value
            print(
                "Warning: scikit-learn not installed — "
                "semantic overlap defaults to 0.5"
            )
            return 0.5

        # Combine all context summaries into one document
        combined_context = " ".join(context_summaries)
        corpus = [query_text, combined_context]

        vectorizer = TfidfVectorizer(stop_words="english")
        try:
            tfidf_matrix = vectorizer.fit_transform(corpus)
        except ValueError:
            # All stop-words or empty after tokenization
            return 0.0

        similarity = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:2])
        return float(similarity[0][0])

    # ------------------------------------------------------------------
    # Expansion
    # ------------------------------------------------------------------

    def _expand_context(
        self,
        current_ids: List[str],
        target_id: str,
        new_radius: int,
    ) -> List[str]:
        """Expand context by increasing BFS radius and adding missing deps."""
        expanded = set(current_ids)

        # 1. Add missing direct dependencies of the target
        for edge in self._outgoing.get(target_id, []):
            if edge.relation in ("calls", "imports"):
                expanded.add(edge.target)

        # 2. Widen BFS neighbourhood
        bfs_nodes = self._bfs(target_id, radius=new_radius)
        expanded.update(bfs_nodes)

        return list(expanded)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_query_entities(self, query_text: str) -> Set[str]:
        """Extract likely code entity names from a natural-language query.

        Looks for CamelCase identifiers, snake_case identifiers, and
        dot-qualified names (e.g. ``MyClass.my_method``).
        """
        # CamelCase words (e.g. IngestionAgent, FileNode)
        camel = set(re.findall(r"\b[A-Z][a-zA-Z0-9]+\b", query_text))
        # snake_case words (e.g. ingest_repository, build_graph)
        snake = set(re.findall(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b", query_text))
        # dot-qualified (e.g. agent.run, MyClass.method)
        dotted = set(re.findall(r"\b[a-zA-Z_]\w*\.\w+\b", query_text))

        entities = camel | snake | dotted
        # Filter out common English words that happen to match CamelCase
        noise = {
            "The", "This", "That", "With", "From", "Into", "When",
            "Does", "Will", "Would", "Could", "Should", "Generate",
            "Create", "Update", "Delete", "Return", "Check", "Find",
        }
        return entities - noise

    def _collect_summaries(self, context_ids: List[str]) -> List[str]:
        """Collect text summaries for context nodes from the compressed graph."""
        summaries = []
        for node_id in context_ids:
            if node_id in self.compressed_graph.node_summaries:
                summaries.append(
                    self.compressed_graph.node_summaries[node_id].summary
                )
            elif node_id in self._node_lookup:
                # Fallback: use node name and type
                node = self._node_lookup[node_id]
                summaries.append(f"{node.type}: {node.name}")
        return summaries

    def _all_pass(self, metrics: SufficiencyMetrics) -> bool:
        return (
            metrics.dependency_completeness >= self.DEP_THRESHOLD
            and metrics.entity_coverage >= self.ENTITY_THRESHOLD
            and metrics.semantic_overlap >= self.SEMANTIC_THRESHOLD
        )

    def _build_result(
        self,
        is_sufficient: bool,
        metrics: SufficiencyMetrics,
        context_ids: List[str],
        rounds: int,
        reason: str,
        query: SufficiencyQuery,
    ) -> SufficiencyResult:
        return SufficiencyResult(
            is_sufficient=is_sufficient,
            metrics=metrics,
            context_node_ids=sorted(context_ids),
            expansion_rounds=rounds,
            max_rounds=self.MAX_ROUNDS,
            thresholds={
                "dependency_completeness": self.DEP_THRESHOLD,
                "entity_coverage": self.ENTITY_THRESHOLD,
                "semantic_overlap": self.SEMANTIC_THRESHOLD,
            },
            reason=reason,
            query=query,
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Run the Context Sufficiency Estimator on a compressed graph."
    )
    parser.add_argument(
        "--link-graph",
        default="data/link_graph.json",
        help="Path to link_graph.json",
    )
    parser.add_argument(
        "--compressed-graph",
        default="data/compressed_graph.json",
        help="Path to compressed_graph.json",
    )
    parser.add_argument(
        "--query",
        default=None,
        help="Query text (default: auto-generated from target node)",
    )
    parser.add_argument(
        "--output",
        default="data/cse_result.json",
        help="Output path for CSE result",
    )

    args = parser.parse_args()

    agent = CSEAgent(args.link_graph, args.compressed_graph)
    target_id, target_file = agent.pick_representative_target()

    query_text = args.query or f"Generate code related to {agent._node_lookup[target_id].name}"
    query = SufficiencyQuery(
        query_text=query_text,
        target_node_id=target_id,
        target_file_path=target_file,
    )

    print(f"Target: {target_id}")
    print(f"Query: {query_text}")
    print()

    result = agent.evaluate(query)
    agent.save_result(result, args.output)

    print(f"\nSufficient: {result.is_sufficient}")
    print(f"Expansion rounds: {result.expansion_rounds}/{result.max_rounds}")
    print(f"Dependency completeness: {result.metrics.dependency_completeness:.2%}")
    print(f"Entity coverage: {result.metrics.entity_coverage:.2%}")
    print(f"Semantic overlap: {result.metrics.semantic_overlap:.2%}")
    print(f"Context nodes: {len(result.context_node_ids)}")
    print(f"Reason: {result.reason}")
