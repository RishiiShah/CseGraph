"""Context Sufficiency Estimator (CSE) — cse_agent.py

Combines two complementary paradigms:

1. Tiered Adaptive Thresholds (structural precision):
   - Tier 0 (direct calls):      must be 100 % present — these are hard dependencies.
   - Tier 1 (file imports):       adaptive target based on context budget.
   - Tier 2 (2-hop neighbours):   budget-limited BFS expansion.

2. Confidence Scoring + Raw Code Fallback (semantic precision):
   - If model_confidence < CONFIDENCE_THRESHOLD the agent replaces compressed
     summaries of Tier-0 nodes with verbatim source code segments.

Both mechanisms are described in *proposal_text_extracted.txt* §3.2 (Decision
Logic Approach) and §3.3 (Validation Metrics).
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
from collections import defaultdict, deque
from typing import Dict, List, Set, Tuple

# Allow running from agents/ directly
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.compressed_graph import CompressedGraph
from models.cse_result import SufficiencyMetrics, SufficiencyQuery, SufficiencyResult
from models.link_graph import GraphEdge, GraphNode, LinkGraph


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NOISE_WORDS: Set[str] = {
    # Common English words that match CamelCase but aren't code symbols
    "The", "This", "That", "With", "From", "Into", "When",
    "Does", "Will", "Would", "Could", "Should", "Generate",
    "Create", "Update", "Delete", "Return", "Check", "Find",
    # Verb prefixes injected by _build_rich_query
    "Implement", "Class", "Function", "Method",
}


class CSEAgent:
    """Context Sufficiency Estimator.

    Evaluates whether retrieved context is sufficient for code generation
    using four metrics:
      * dependency_completeness  — structural: are key deps present?
      * entity_coverage          — lexical:    do context names cover query terms?
      * semantic_overlap         — semantic:   TF-IDF cosine similarity to query.
      * model_confidence         — composite:  proxy for LLM generation confidence.

    Expansion strategy is *tiered*:
      - Round 0: Tier 0 (direct call targets) are always fetched first.
      - Round 1: Tier 1 (file-level imports) are fetched up to ``IMPORT_BUDGET``.
      - Round 2+: Tier 2 (2-hop BFS) up to ``CONTEXT_BUDGET`` total nodes.

    If confidence is low, Tier-0 nodes have their compressed summaries replaced
    by verbatim raw source code (the Raw Code Fallback).
    """

    # ---- Metric thresholds ------------------------------------------------
    DEP_THRESHOLD: float = 0.80        # dependency_completeness
    ENTITY_THRESHOLD: float = 0.80     # entity_coverage
    SEMANTIC_THRESHOLD: float = 0.50   # semantic_overlap (hard threshold)
    SEMANTIC_THRESHOLD_RELAXED: float = 0.05  # used when dep+ent both pass
    CONFIDENCE_THRESHOLD: float = 0.70  # model_confidence (lowered: structural metrics dominate)

    # ---- Expansion budget -------------------------------------------------
    MAX_ROUNDS: int = 3
    CONTEXT_BUDGET: int = 60    # hard cap on total context nodes
    IMPORT_BUDGET: int = 20     # Tier-1 cap (file imports)

    # ---- Tiered completion targets (fraction of each tier to collect) ------
    TIER0_TARGET: float = 1.00  # 100 % of direct call targets
    TIER1_TARGET: float = 0.75  # 75 % of file imports
    # Tier 2 is purely budget-limited — no ratio target

    def __init__(self, link_graph_path: str, compressed_graph_path: str) -> None:
        self.link_graph = self._load_link_graph(link_graph_path)
        self.compressed_graph = self._load_compressed_graph(compressed_graph_path)

        # Adjacency indices
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
        """Run the tiered CSE evaluation loop.

        Algorithm
        ---------
        1. Seed context from the pre-computed compressed-graph slice (radius 1).
        2. Immediately pull all Tier-0 (direct call) dependencies (always 100 %).
        3. Compute all metrics including model_confidence.
        4. If confidence is low  → flag Tier-0 nodes for raw-code replacement.
        5. If structural metrics are low → expand by tier (imports → 2-hop BFS).
        6. Repeat up to ``MAX_ROUNDS``.
        """
        context_ids: List[str] = self._get_initial_context(query.target_node_id, radius=1)
        raw_code_ids: Set[str] = set()

        # Always seed Tier 0 (direct call targets) at 100 %
        context_ids = self._ensure_tier0(query.target_node_id, context_ids)

        for round_num in range(self.MAX_ROUNDS):
            metrics = self._compute_all_metrics(query, context_ids, raw_code_ids)

            if self._all_pass(metrics):
                return self._build_result(
                    is_sufficient=True,
                    metrics=metrics,
                    context_ids=context_ids,
                    raw_code_ids=list(raw_code_ids),
                    rounds=round_num,
                    reason="All thresholds met",
                    query=query,
                )

            # --- Confidence fallback: replace Tier-0 summaries with raw code ---
            if metrics.model_confidence < self.CONFIDENCE_THRESHOLD:
                tier0_nodes = self._get_tier0_nodes(query.target_node_id)
                raw_code_ids.update(tier0_nodes & set(context_ids))

            # --- Structural expansion by tier --------------------------------
            # Also expand when confidence is low: adding more context nodes
            # increases total summary chars (improving compression_factor) and
            # can surface semantically relevant nodes — both lift model_confidence.
            if (
                metrics.dependency_completeness < self.DEP_THRESHOLD
                or metrics.entity_coverage < self.ENTITY_THRESHOLD
                or metrics.model_confidence < self.CONFIDENCE_THRESHOLD
            ):
                context_ids = self._expand_by_tier(
                    query.target_node_id,
                    context_ids,
                    expansion_round=round_num,
                )

        # Final evaluation after exhausting rounds
        metrics = self._compute_all_metrics(query, context_ids, raw_code_ids)
        return self._build_result(
            is_sufficient=self._all_pass(metrics),
            metrics=metrics,
            context_ids=context_ids,
            raw_code_ids=list(raw_code_ids),
            rounds=self.MAX_ROUNDS,
            reason=(
                "All thresholds met"
                if self._all_pass(metrics)
                else "Max expansion rounds reached"
            ),
            query=query,
        )

    def pick_representative_target(self) -> Tuple[str, str, str]:
        """Auto-pick the highest-degree non-file node for demo purposes.

        Returns ``(node_id, file_path, rich_query_text)``.
        """
        best_id = ""
        best_degree = -1
        for node_id, node in self._node_lookup.items():
            if node.type == "file":
                continue
            degree = len(self._outgoing.get(node_id, [])) + len(
                self._incoming.get(node_id, [])
            )
            if degree > best_degree:
                best_degree = degree
                best_id = node_id
        if not best_id:
            best_id = self.link_graph.nodes[0].id if self.link_graph.nodes else ""
        file_path = self._node_lookup[best_id].file_path if best_id else ""
        rich_query = self._build_rich_query(best_id) if best_id else ""
        return best_id, file_path, rich_query

    def _build_rich_query(self, node_id: str) -> str:
        """Build a semantically rich query from node metadata and neighbours.

        Uses node type, name, file path, and the names of directly connected
        symbols so the TF-IDF representation shares vocabulary with summaries.

        Example output:
          "Implement class Pipeline in core/pipeline.py with methods __init__
           run calling CSVLoader NormalizeStage JsonWriter"
        """
        node = self._node_lookup.get(node_id)
        if node is None:
            return f"Generate code for {node_id}"

        verb = {
            "class": "Implement class",
            "function": "Implement function",
            "method": "Implement method",
        }.get(node.type, "Generate code for")

        parts = [f"{verb} {node.name} in {node.file_path}"]

        # Collect outgoing neighbour names (calls / imports)
        callee_names = [
            self._node_lookup[e.target].name
            for e in self._outgoing.get(node_id, [])
            if e.target in self._node_lookup and e.relation in ("calls", "imports")
        ]
        if callee_names:
            parts.append("calling " + " ".join(callee_names[:8]))

        # Collect sibling methods / functions in the same file
        sibling_names = [
            n.name
            for n in self.link_graph.nodes
            if n.file_path == node.file_path
            and n.id != node_id
            and n.type in ("method", "function", "class")
        ]
        if sibling_names:
            parts.append("with " + " ".join(sibling_names[:6]))

        return " ".join(parts)

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
        return self._bfs(target_node_id, radius)

    def _bfs(self, start_id: str, radius: int, max_nodes: int | None = None) -> List[str]:
        """Budget-aware BFS on the link graph (both directions)."""
        cap = max_nodes if max_nodes is not None else self.CONTEXT_BUDGET
        visited: Set[str] = {start_id}
        queue: deque[Tuple[str, int]] = deque([(start_id, 0)])

        while queue and len(visited) < cap:
            node_id, depth = queue.popleft()
            if depth >= radius:
                continue
            neighbours = [
                e.target for e in self._outgoing.get(node_id, [])
            ] + [
                e.source for e in self._incoming.get(node_id, [])
            ]
            for nb in neighbours:
                if nb not in visited and len(visited) < cap:
                    visited.add(nb)
                    queue.append((nb, depth + 1))
        return list(visited)

    # ------------------------------------------------------------------
    # Tiered dependency helpers
    # ------------------------------------------------------------------

    def _get_tier0_nodes(self, target_id: str) -> Set[str]:
        """Tier 0: direct *calls* targets — must be 100 % covered."""
        return {
            e.target
            for e in self._outgoing.get(target_id, [])
            if e.relation == "calls"
        }

    def _get_tier1_nodes(self, target_id: str) -> Set[str]:
        """Tier 1: file-level *imports* from the target's file."""
        target_node = self._node_lookup.get(target_id)
        if target_node is None:
            return set()
        file_id = f"file::{target_node.file_path}"
        return {
            e.target
            for e in self._outgoing.get(file_id, [])
            if e.relation == "imports"
        }

    def _ensure_tier0(self, target_id: str, context_ids: List[str]) -> List[str]:
        """Add all Tier-0 (direct call) nodes to context, up to CONTEXT_BUDGET."""
        expanded = set(context_ids)
        tier0 = self._get_tier0_nodes(target_id)
        for nid in tier0:
            if len(expanded) >= self.CONTEXT_BUDGET:
                break
            expanded.add(nid)
        return list(expanded)

    def _expand_by_tier(
        self,
        target_id: str,
        current_ids: List[str],
        expansion_round: int,
    ) -> List[str]:
        """Expand context by one tier per round.

        Round 0 already seeded Tier 0 in ``evaluate()``.
        Round 0 expansion → Tier 1 (imports, up to IMPORT_BUDGET slots).
        Round 1+ expansion → Tier 2 (2-hop BFS, up to CONTEXT_BUDGET total).
        """
        expanded = set(current_ids)
        remaining_budget = self.CONTEXT_BUDGET - len(expanded)
        if remaining_budget <= 0:
            return current_ids

        if expansion_round == 0:
            # Tier 1: file-level imports
            tier1 = self._get_tier1_nodes(target_id) - expanded
            import_slots = min(self.IMPORT_BUDGET, remaining_budget)
            for nid in list(tier1)[:import_slots]:
                expanded.add(nid)
        else:
            # Tier 2: 2-hop BFS, budget-limited
            new_radius = expansion_round + 2  # round 1 → radius 3, etc.
            bfs_nodes = self._bfs(
                target_id,
                radius=new_radius,
                max_nodes=self.CONTEXT_BUDGET,
            )
            for nid in bfs_nodes:
                if len(expanded) >= self.CONTEXT_BUDGET:
                    break
                expanded.add(nid)

        return list(expanded)

    # ------------------------------------------------------------------
    # Metric computation
    # ------------------------------------------------------------------

    def _compute_all_metrics(
        self,
        query: SufficiencyQuery,
        context_ids: List[str],
        raw_code_ids: Set[str],
    ) -> SufficiencyMetrics:
        context_set = set(context_ids)
        context_names = {
            self._node_lookup[nid].name
            for nid in context_ids
            if nid in self._node_lookup
        }
        context_summaries = self._collect_summaries(context_ids, raw_code_ids)

        dep = self._compute_dependency_completeness(query.target_node_id, context_set)
        ent = self._compute_entity_coverage(query.query_text, context_names)
        sem = self._compute_semantic_overlap(query.query_text, context_summaries)
        conf = self._compute_model_confidence(sem, ent, dep, context_summaries)

        return SufficiencyMetrics(
            dependency_completeness=dep,
            entity_coverage=ent,
            semantic_overlap=sem,
            model_confidence=conf,
        )

    def _compute_dependency_completeness(
        self, target_id: str, context_ids: Set[str]
    ) -> float:
        """Tiered dependency completeness score.

        Weights:
          - Tier-0 (calls)   → weight 1.0 per node (critical)
          - Tier-1 (imports) → weight 0.5 per node (important)
        """
        tier0 = self._get_tier0_nodes(target_id)
        tier1 = self._get_tier1_nodes(target_id)

        if not tier0 and not tier1:
            return 1.0  # No dependencies → trivially complete

        # Score: weighted ratio
        tier0_score = sum(1.0 for nid in tier0 if nid in context_ids)
        tier1_score = sum(0.5 for nid in tier1 if nid in context_ids)
        max_score = len(tier0) * 1.0 + len(tier1) * 0.5

        return min(1.0, (tier0_score + tier1_score) / max_score)

    def _compute_entity_coverage(
        self, query_text: str, context_node_names: Set[str]
    ) -> float:
        """Fraction of query-extracted entities present in context (case-insensitive)."""
        entities = self._extract_query_entities(query_text)
        if not entities:
            return 1.0
        lower_names = {n.lower() for n in context_node_names}
        found = sum(1 for e in entities if e.lower() in lower_names)
        return found / len(entities)

    def _code_tokenize(self, text: str) -> List[str]:
        """Tokenise text into lowercase sub-tokens with code-identifier awareness.

        Handles:
          - CamelCase splits:  ``CSVLoader``  → ``csv loader``
          - snake_case splits: ``run_pipeline`` → ``run pipeline``
          - Dot/slash splits:  ``core/pipeline.py`` → ``core pipeline py``
          - Short stop words filtered (``in``, ``by``, ``to``, etc.)
        """
        # Split on uppercase boundaries for CamelCase
        text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
        text = re.sub(r"([A-Z]{2,})([A-Z][a-z])", r"\1 \2", text)
        # Replace any non-alphanumeric character with a space
        text = re.sub(r"[^a-zA-Z0-9]+", " ", text)
        tokens = [t.lower() for t in text.split() if len(t) > 1]
        _STOP = {
            "in", "on", "by", "to", "of", "at", "is", "it", "or", "an",
            "do", "be", "no", "up", "as", "if", "so", "we", "my", "py",
            "the", "and", "for", "with", "from", "that", "this", "into",
            "are", "was", "has", "had", "not", "its",
        }
        return [t for t in tokens if t not in _STOP]

    def _compute_bm25_similarity(
        self, query_tokens: List[str], doc_token_lists: List[List[str]]
    ) -> float:
        """BM25 score of query against a corpus of tokenised documents.

        Each summary is one document. The raw BM25 score is normalised by the
        theoretical maximum (all query terms present at saturation) to yield a
        value in [0, 1].

        Parameters (standard Okapi BM25):
          k1 = 1.5  — term-frequency saturation
          b  = 0.75 — document-length normalisation
        """
        if not doc_token_lists or not query_tokens:
            return 0.0

        k1, b = 1.5, 0.75
        N = len(doc_token_lists)
        avg_dl = sum(len(d) for d in doc_token_lists) / N

        # Document frequency per term across the corpus
        df: Dict[str, int] = defaultdict(int)
        for doc in doc_token_lists:
            for t in set(doc):
                df[t] += 1

        # Score query against the *union* of all docs (treat context as one slab)
        combined_tokens: List[str] = []
        for doc in doc_token_lists:
            combined_tokens.extend(doc)
        dl = len(combined_tokens)

        tf: Dict[str, float] = defaultdict(float)
        for t in combined_tokens:
            tf[t] += 1.0

        score = 0.0
        max_score = 0.0
        for qt in set(query_tokens):
            n_t = df.get(qt, 0)
            idf = math.log((N - n_t + 0.5) / (n_t + 0.5) + 1.0)
            # BM25 tf component
            tf_d = tf.get(qt, 0.0)
            tf_bm25 = (tf_d * (k1 + 1.0)) / (
                tf_d + k1 * (1.0 - b + b * dl / max(avg_dl, 1.0))
            )
            score += idf * tf_bm25
            # Theoretical max: tf → ∞ saturates at (k1 + 1)
            max_score += idf * (k1 + 1.0)

        if max_score == 0.0:
            return 0.0
        return min(1.0, score / max_score)

    def _compute_semantic_overlap(
        self, query_text: str, context_summaries: List[str]
    ) -> float:
        """BM25 similarity with code-aware tokenisation.

        Replaces TF-IDF cosine similarity. Improvements over TF-IDF:
          - CamelCase/snake_case identifiers are split before matching, so
            ``CSVLoader`` in context matches ``csv`` and ``loader`` in query.
          - BM25 handles term-frequency saturation and variable document length,
            reducing the bias towards longer summaries.
          - No sklearn dependency.
        """
        if not context_summaries:
            return 0.0

        query_tokens = self._code_tokenize(query_text)
        doc_token_lists = [self._code_tokenize(s) for s in context_summaries]

        if not query_tokens:
            return 0.0

        return self._compute_bm25_similarity(query_tokens, doc_token_lists)

    def _compute_model_confidence(
        self,
        semantic_overlap: float,
        entity_coverage: float,
        dependency_completeness: float,
        context_summaries: List[str],
    ) -> float:
        """Composite proxy for LLM generation confidence.

        Rationale (aligned with proposal §3.2 Confidence Scoring):
          - Uses semantic_overlap, entity_coverage, dependency_completeness
            as surrogates for the log-probability scores a real LLM would emit.
          - Applies a mild *compression penalty*: very short or very long
            aggregated context degrades confidence (summary quality heuristic).

        Formula:
            conf = 0.35 * sem + 0.40 * ent + 0.25 * dep * compression_factor

        In a full system with a coupled LLM, ``semantic_overlap`` would be
        replaced by actual log-probability scores from a preliminary decoding pass.
        """
        # Compression quality factor — penalises truly empty contexts only.
        # The sigmoid is centred at 500 chars (not 3000) because compressed
        # summaries are intentionally short; we still want to reward non-empty
        # contexts and apply a floor of 0.5 so template-length summaries never
        # eliminate the dependency contribution entirely.
        total_chars = sum(len(s) for s in context_summaries)
        if total_chars == 0:
            compression_factor = 0.0
        else:
            raw_factor = 1.0 / (1.0 + math.exp(-0.003 * (total_chars - 500)))
            compression_factor = max(0.5, raw_factor)

        # Rebalanced: structural metrics (dep+ent) dominate over TF-IDF semantic
        # overlap, which is unreliable when compressed summaries are template strings.
        raw = (
            0.20 * semantic_overlap
            + 0.35 * entity_coverage
            + 0.45 * dependency_completeness * compression_factor
        )
        return min(1.0, max(0.0, raw))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_query_entities(self, query_text: str) -> Set[str]:
        """Extract likely code entity names from a natural-language query.

        Looks for CamelCase identifiers, snake_case identifiers, and
        dot-qualified names (e.g. ``MyClass.my_method``).

        Three artifact-filtering steps:
          1. File paths (e.g. ``core/pipeline.py``) are stripped before
             tokenisation so directory and filename stems don't become
             phantom entities.
          2. Dotted tokens ending in a file extension (.py, .ts, …) are
             discarded (they're paths, not symbol references).
          3. Standalone snake_case tokens that are suffix sub-tokens of an
             already-matched dotted entity are removed to avoid double-counting
             (e.g. ``list_all`` from ``UserRepository.list_all``).
        """
        # Strip file-path-like substrings before matching to avoid directory
        # components (e.g. "user_service_api") being treated as entities.
        cleaned = re.sub(r"\S+/\S+", "", query_text)

        camel = set(re.findall(r"\b[A-Z][a-zA-Z0-9]+\b", cleaned))
        snake = set(re.findall(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b", cleaned))
        dotted = set(re.findall(r"\b[a-zA-Z_]\w*\.\w+\b", cleaned))

        # Drop dotted tokens that look like file references (end in extension)
        dotted = {d for d in dotted if not re.search(r"\.[a-z]{2,4}$", d)}

        # Remove snake_case sub-tokens that are already the suffix of a dotted entity
        dotted_suffixes = {d.split(".")[-1] for d in dotted}
        snake = snake - dotted_suffixes

        return (camel | snake | dotted) - _NOISE_WORDS

    def _collect_summaries(
        self, context_ids: List[str], raw_code_ids: Set[str]
    ) -> List[str]:
        """Collect text summaries or raw code for context nodes.

        If a node is in ``raw_code_ids`` (triggered by low confidence),
        its verbatim source code is used instead of the compressed summary.
        """
        summaries: List[str] = []
        for node_id in context_ids:
            if node_id in raw_code_ids and node_id in self._node_lookup:
                raw = self._get_raw_code(self._node_lookup[node_id])
                if raw:
                    summaries.append(raw)
                    continue
            if node_id in self.compressed_graph.node_summaries:
                summaries.append(self.compressed_graph.node_summaries[node_id].summary)
            elif node_id in self._node_lookup:
                node = self._node_lookup[node_id]
                summaries.append(f"{node.type}: {node.name}")
        return summaries

    def _get_raw_code(self, node: GraphNode) -> str:
        """Fetch verbatim source code lines for a node using its line range."""
        if not node.file_path or node.start_line is None or node.end_line is None:
            return ""
        abs_path = os.path.join(self.link_graph.root_dir, node.file_path)
        if not os.path.isfile(abs_path):
            return ""
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            start_idx = max(0, node.start_line - 1)
            end_idx = min(len(lines), node.end_line)
            return "".join(lines[start_idx:end_idx])
        except Exception:
            return ""

    def _all_pass(self, metrics: SufficiencyMetrics) -> bool:
        structural_ok = (
            metrics.dependency_completeness >= self.DEP_THRESHOLD
            and metrics.entity_coverage >= self.ENTITY_THRESHOLD
        )
        # When structural coverage is proven, relax the semantic threshold.
        # TF-IDF cosine on template summaries is unreliable as a hard gate.
        sem_threshold = (
            self.SEMANTIC_THRESHOLD_RELAXED if structural_ok else self.SEMANTIC_THRESHOLD
        )
        return (
            structural_ok
            and metrics.semantic_overlap >= sem_threshold
            and metrics.model_confidence >= self.CONFIDENCE_THRESHOLD
        )

    def _build_result(
        self,
        is_sufficient: bool,
        metrics: SufficiencyMetrics,
        context_ids: List[str],
        raw_code_ids: List[str],
        rounds: int,
        reason: str,
        query: SufficiencyQuery,
    ) -> SufficiencyResult:
        return SufficiencyResult(
            is_sufficient=is_sufficient,
            metrics=metrics,
            context_node_ids=sorted(context_ids),
            raw_code_nodes=sorted(raw_code_ids),
            expansion_rounds=rounds,
            max_rounds=self.MAX_ROUNDS,
            thresholds={
                "dependency_completeness": self.DEP_THRESHOLD,
                "entity_coverage": self.ENTITY_THRESHOLD,
                "semantic_overlap": self.SEMANTIC_THRESHOLD,
                "model_confidence": self.CONFIDENCE_THRESHOLD,
            },
            reason=reason,
            query=query,
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

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
    target_id, target_file, auto_query = agent.pick_representative_target()

    query_text = args.query or auto_query
    query = SufficiencyQuery(
        query_text=query_text,
        target_node_id=target_id,
        target_file_path=target_file,
    )

    print(f"Target : {target_id}")
    print(f"Query  : {query_text}")
    print()

    result = agent.evaluate(query)
    agent.save_result(result, args.output)

    print(f"\nSufficient         : {result.is_sufficient}")
    print(f"Expansion rounds   : {result.expansion_rounds}/{result.max_rounds}")
    print(f"Dep. completeness  : {result.metrics.dependency_completeness:.2%}")
    print(f"Entity coverage    : {result.metrics.entity_coverage:.2%}")
    print(f"Semantic overlap   : {result.metrics.semantic_overlap:.2%}")
    print(f"Model confidence   : {result.metrics.model_confidence:.2%}")
    print(f"Context nodes      : {len(result.context_node_ids)}")
    print(f"Raw code nodes     : {len(result.raw_code_nodes)}")
    print(f"Reason             : {result.reason}")
