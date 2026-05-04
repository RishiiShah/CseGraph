from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
from collections import defaultdict, deque
from typing import Callable, Dict, List, Optional, Set, Tuple

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.compressed_graph import CompressedGraph
from models.cse_result import SufficiencyMetrics, SufficiencyQuery, SufficiencyResult
from models.link_graph import GraphEdge, GraphNode, LinkGraph
from system_profile import build_system_profile, embedding_device

# ---------------------------------------------------------------------------
# Sentence-embedding model — lazy singleton, loaded on first use.
# Device (mps / cuda / cpu) is derived from the system profile so embeddings
# run on whatever accelerator is available.
# ---------------------------------------------------------------------------
_EMBED_MODEL = None          # SentenceTransformer instance | False (unavailable)
_EMBED_MODEL_NAME = "BAAI/bge-small-en-v1.5"  # fast 33M-param model; good on code identifiers
_EMBED_CACHE: Dict[str, "np.ndarray"] = {}    # text-hash → embedding vector
_EMBED_DEVICE: Optional[str] = None           # resolved once on first model load


def _get_embed_model():
    """Return the singleton SentenceTransformer on the right device, or None.

    The device (mps / cuda / cpu) is resolved once from the system profile and
    cached so subsequent calls don't re-probe hardware.
    """
    global _EMBED_MODEL, _EMBED_DEVICE
    if _EMBED_MODEL is None:
        try:
            from sentence_transformers import SentenceTransformer
            if _EMBED_DEVICE is None:
                _EMBED_DEVICE = embedding_device(build_system_profile())
            _EMBED_MODEL = SentenceTransformer(_EMBED_MODEL_NAME, device=_EMBED_DEVICE)
            print(f"[CSEAgent] Embedding model loaded on device={_EMBED_DEVICE}")
        except Exception:
            _EMBED_MODEL = False  # don't retry
    return _EMBED_MODEL if _EMBED_MODEL is not False else None


def _embed(texts: List[str]) -> Optional["np.ndarray"]:
    """Encode a list of texts to a (N, D) numpy array, using an in-memory cache."""
    model = _get_embed_model()
    if model is None or not texts:
        return None
    results: List["np.ndarray"] = []
    to_encode: List[Tuple[int, str]] = []
    for i, t in enumerate(texts):
        key = hashlib.md5(t.encode()).hexdigest()
        if key in _EMBED_CACHE:
            results.append((i, _EMBED_CACHE[key]))
        else:
            to_encode.append((i, t, key))

    if to_encode:
        vecs = model.encode([t for _, t, _ in to_encode], show_progress_bar=False)
        for (i, t, key), vec in zip(to_encode, vecs):
            _EMBED_CACHE[key] = vec
            results.append((i, vec))

    results.sort(key=lambda x: x[0])
    return np.stack([v for _, v in results])


# Constants
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

    # Metric thresholds
    DEP_THRESHOLD: float = 0.80        # dependency_completeness
    ENTITY_THRESHOLD: float = 0.80     # entity_coverage
    SEMANTIC_THRESHOLD: float = 0.50   # semantic_overlap (hard threshold)
    SEMANTIC_THRESHOLD_RELAXED: float = 0.0   # not gated when dep+ent both pass
    CONFIDENCE_THRESHOLD: float = 0.70  # model_confidence (lowered: structural metrics dominate)

    # Expansion budget
    MAX_ROUNDS: int = 3
    CONTEXT_BUDGET: int = 60    # hard cap on total context nodes
    IMPORT_BUDGET: int = 20     # Tier-1 cap (file imports)

    # Tiered completion targets (fraction of each tier to collect)
    TIER0_TARGET: float = 1.00  # 100 % of direct call targets
    TIER1_TARGET: float = 0.75  # 75 % of file imports
    # Tier 2 is purely budget-limited — no ratio target

    # Recompression trigger
    CONFIDENCE_DROP_THRESHOLD: float = 0.15  # > 15% triggers resummary

    def __init__(
        self,
        link_graph_path: str,
        compressed_graph_path: str,
        resummary_fn: Optional[Callable[[str], str]] = None,
    ) -> None:
        """
        Parameters
        ----------
        link_graph_path:
            Path to the serialised LinkGraph JSON.
        compressed_graph_path:
            Path to the serialised CompressedGraph JSON.
        resummary_fn:
            Optional callable ``(node_id: str) -> str`` that regenerates a
            fresh summary for a given node.  When provided, a confidence drop
            of more than ``CONFIDENCE_DROP_THRESHOLD`` between expansion rounds
            triggers re-summarisation of newly added context nodes in-memory,
            implementing the proposal's "recompress on confidence drop" rule.
            Typically ``CompressionAgent._generate_node_summary`` is passed here.
        """
        self._resummary_fn = resummary_fn
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

    # Loading helpers
    @staticmethod
    def _load_link_graph(path: str) -> LinkGraph:
        with open(path, "r", encoding="utf-8") as f:
            return LinkGraph(**json.load(f))

    @staticmethod
    def _load_compressed_graph(path: str) -> CompressedGraph:
        with open(path, "r", encoding="utf-8") as f:
            return CompressedGraph(**json.load(f))

    # Public API
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

        prev_confidence: Optional[float] = None
        prev_context_set: Set[str] = set(context_ids)
        recompressed_rounds: int = 0

        for round_num in range(self.MAX_ROUNDS):
            metrics = self._compute_all_metrics(query, context_ids, raw_code_ids)

            # Confidence-drop → recompress newly added nodes
            # If confidence fell by more than CONFIDENCE_DROP_THRESHOLD since the
            # last round, the newly pulled-in nodes likely have poor template
            # summaries.  Regenerate them in-memory (proposal §3.2: "recompress").
            if (
                prev_confidence is not None
                and prev_confidence - metrics.model_confidence > self.CONFIDENCE_DROP_THRESHOLD
                and self._resummary_fn is not None
            ):
                newly_added = set(context_ids) - prev_context_set
                self._recompress_nodes(newly_added)
                recompressed_rounds += 1
                # Re-evaluate metrics with fresh summaries
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
                    recompressed_rounds=recompressed_rounds,
                )

            # --- Confidence fallback: replace Tier-0 summaries with raw code ---
            if metrics.model_confidence < self.CONFIDENCE_THRESHOLD:
                tier0_nodes = self._get_tier0_nodes(query.target_node_id)
                raw_code_ids.update(tier0_nodes & set(context_ids))

            prev_confidence = metrics.model_confidence
            prev_context_set = set(context_ids)

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
            recompressed_rounds=recompressed_rounds,
        )

    def pick_representative_target(self) -> Tuple[str, str, str]:
        """Auto-pick the highest-degree non-file node for demo purposes.

        Returns ``(node_id, file_path, rich_query_text)``.
        """
        targets = self.pick_top_n_targets(n=1)
        if targets:
            return targets[0]
        return "", "", ""

    def pick_top_n_targets(self, n: int = 3) -> List[Tuple[str, str, str]]:
        """
        Selects the highest-degree node from each file first (one per file),
        then fills remaining slots with the next highest-degree nodes overall.

        Returns a list of ``(node_id, file_path, rich_query_text)`` tuples.
        """
        scored = sorted(
            [
                (
                    len(self._outgoing.get(nid, [])) + len(self._incoming.get(nid, [])),
                    nid,
                )
                for nid, node in self._node_lookup.items()
                if node.type != "file"
            ],
            reverse=True,
        )

        chosen: List[str] = []
        seen_files: Set[str] = set()

        # First pass: one per file for diversity
        for _, nid in scored:
            if len(chosen) >= n:
                break
            fp = self._node_lookup[nid].file_path
            if fp not in seen_files:
                seen_files.add(fp)
                chosen.append(nid)

        # Second pass: fill remaining slots with next highest-degree nodes
        if len(chosen) < n:
            chosen_set = set(chosen)
            for _, nid in scored:
                if len(chosen) >= n:
                    break
                if nid not in chosen_set:
                    chosen.append(nid)
                    chosen_set.add(nid)

        if not chosen and self.link_graph.nodes:
            chosen = [self.link_graph.nodes[0].id]

        return [
            (nid, self._node_lookup[nid].file_path, self._build_rich_query(nid))
            for nid in chosen
        ]

    def _build_rich_query(self, node_id: str) -> str:
        """Build a semantically rich query from node metadata and neighbours.

        Uses node type, name, file path, and the names of directly connected
        symbols so the TF-IDF representation shares vocabulary with summaries.
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

        callee_names = [
            self._node_lookup[e.target].name
            for e in self._outgoing.get(node_id, [])
            if e.target in self._node_lookup and e.relation in ("calls", "imports")
        ]
        if callee_names:
            parts.append("calling " + " ".join(callee_names[:8]))

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

    # Context retrieval
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

    # Tiered dependency helpers
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
            new_radius = expansion_round + 2
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

    # Metric computation
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
        """Fraction of query-extracted entities present in context (case-insensitive).

        Only tokens that actually match a node name somewhere in the full graph are
        counted as code entities.  Plain English words that happen to be capitalised
        (e.g. "Add", "Fix") are discarded, so natural-language task descriptions are
        not penalised when no explicit symbol references are made.
        """
        entities = self._extract_query_entities(query_text)
        if not entities:
            return 1.0

        # Keep only tokens that match an actual symbol in the codebase
        all_node_names_lower = {n.name.lower() for n in self.link_graph.nodes}
        code_entities = {e for e in entities if e.lower() in all_node_names_lower}

        if not code_entities:
            # Natural-language task with no explicit symbol references — not penalised
            return 1.0

        lower_context = {n.lower() for n in context_node_names}
        found = sum(1 for e in code_entities if e.lower() in lower_context)
        return found / len(code_entities)

    def _code_tokenize(self, text: str) -> List[str]:
        """Tokenise text into lowercase sub-tokens with code-identifier awareness.

        Used by StaticRAGAgent for BM25 node ranking. Handles CamelCase,
        snake_case, and dot/slash splits; filters short stop words.
        """
        text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
        text = re.sub(r"([A-Z]{2,})([A-Z][a-z])", r"\1 \2", text)
        text = re.sub(r"[^a-zA-Z0-9]+", " ", text)
        tokens = [t.lower() for t in text.split() if len(t) > 1]
        _STOP = {
            "in", "on", "by", "to", "of", "at", "is", "it", "or", "an",
            "do", "be", "no", "up", "as", "if", "so", "we", "my", "py",
            "the", "and", "for", "with", "from", "that", "this", "into",
            "are", "was", "has", "had", "not", "its",
        }
        return [t for t in tokens if t not in _STOP]

    def _compute_tfidf_similarity(
        self, query_text: str, context_summaries: List[str]
    ) -> float:
        """TF-IDF cosine similarity: query vs mean of context summary vectors."""
        corpus = [query_text] + context_summaries
        try:
            vectorizer = TfidfVectorizer(token_pattern=r"(?u)\b\w+\b")
            tfidf_matrix = vectorizer.fit_transform(corpus)
        except ValueError:
            return 0.0
        query_vec = tfidf_matrix[0]
        mean_summary_vec = np.asarray(tfidf_matrix[1:].mean(axis=0))
        sim = cosine_similarity(query_vec, mean_summary_vec)[0][0]
        return float(min(1.0, max(0.0, sim)))

    def _compute_embedding_similarity(
        self, query_text: str, context_summaries: List[str]
    ) -> Optional[float]:
        """Sentence-embedding cosine similarity: query vs mean of context embeddings.

        Returns None when the embedding model is unavailable so the caller can
        fall back to TF-IDF only.
        """
        vecs = _embed([query_text] + context_summaries)
        if vecs is None:
            return None
        query_vec = vecs[0:1]           # (1, D)
        mean_ctx_vec = vecs[1:].mean(axis=0, keepdims=True)  # (1, D)
        sim = cosine_similarity(query_vec, mean_ctx_vec)[0][0]
        return float(min(1.0, max(0.0, sim)))

    def _compute_semantic_overlap(
        self, query_text: str, context_summaries: List[str]
    ) -> float:
        """Blended semantic similarity: 0.6 * embedding + 0.4 * TF-IDF.

        Embedding cosine (sentence-transformers) captures what the code *does*.
        TF-IDF cosine captures exact identifier names, which matter a lot in code
        (e.g. the query mentions ``CSEAgent`` and the summary contains it).
        Blending keeps both signals. Falls back to TF-IDF only when the
        embedding model is not installed.
        """
        if not context_summaries or not query_text:
            return 0.0

        tfidf_sim = self._compute_tfidf_similarity(query_text, context_summaries)
        emb_sim = self._compute_embedding_similarity(query_text, context_summaries)

        if emb_sim is None:
            return tfidf_sim
        return 0.6 * emb_sim + 0.4 * tfidf_sim

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

    # Helpers
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

    def _recompress_nodes(self, node_ids: Set[str]) -> None:
        """Regenerate summaries for *node_ids* via ``self._resummary_fn``.

        Called when a confidence drop of > ``CONFIDENCE_DROP_THRESHOLD`` is
        detected between rounds, indicating that newly pulled-in nodes have
        low-quality template summaries.  Updates ``self.compressed_graph``
        in-memory; does not touch disk.
        """
        if self._resummary_fn is None or not node_ids:
            return
        for nid in node_ids:
            fresh = self._resummary_fn(nid)
            if not fresh:
                continue
            if nid in self.compressed_graph.node_summaries:
                self.compressed_graph.node_summaries[nid].summary = fresh
            elif nid in self._node_lookup:
                node = self._node_lookup[nid]
                from models.compressed_graph import NodeSummary as _NS
                self.compressed_graph.node_summaries[nid] = _NS(
                    node_id=nid,
                    name=node.name,
                    node_type=node.type,
                    file_path=node.file_path,
                    summary=fresh,
                )

    def expand_for_query(
        self,
        query: SufficiencyQuery,
        context_ids: List[str],
        raw_code_ids: List[str],
        reason_prefix: str = "Logprob-triggered re-expansion",
    ) -> SufficiencyResult:
        """One additional expansion step beyond the normal evaluation loop.

        Used externally — typically after a logprob signal of low generation
        confidence — to widen the context and return a new SufficiencyResult
        ready for a fresh codegen call.

        Parameters
        ----------
        query:
            The original SufficiencyQuery (target + text unchanged).
        context_ids:
            Node IDs from the previous evaluation round to expand from.
        raw_code_ids:
            Raw-code node set from the previous evaluation (preserved).
        reason_prefix:
            Prefix for the ``reason`` field in the returned result.
        """
        expanded_ids = self._expand_by_tier(
            query.target_node_id,
            context_ids,
            expansion_round=self.MAX_ROUNDS,
        )
        metrics = self._compute_all_metrics(query, expanded_ids, set(raw_code_ids))
        return self._build_result(
            is_sufficient=self._all_pass(metrics),
            metrics=metrics,
            context_ids=expanded_ids,
            raw_code_ids=raw_code_ids,
            rounds=self.MAX_ROUNDS + 1,
            reason=f"{reason_prefix}: {len(expanded_ids)} context nodes",
            query=query,
        )

    def _all_pass(self, metrics: SufficiencyMetrics) -> bool:
        structural_ok = (
            metrics.dependency_completeness >= self.DEP_THRESHOLD
            and metrics.entity_coverage >= self.ENTITY_THRESHOLD
        )
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
        recompressed_rounds: int = 0,
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
            recompressed_rounds=recompressed_rounds,
        )


# CLI entry point
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

    print(f"Target: {target_id}")
    print(f"Query: {query_text}")
    print()

    result = agent.evaluate(query)
    agent.save_result(result, args.output)

    print(f"\nSufficient: {result.is_sufficient}")
    print(f"Expansion rounds: {result.expansion_rounds}/{result.max_rounds}")
    print(f"Dep. completeness: {result.metrics.dependency_completeness:.2%}")
    print(f"Entity coverage: {result.metrics.entity_coverage:.2%}")
    print(f"Semantic overlap: {result.metrics.semantic_overlap:.2%}")
    print(f"Model confidence: {result.metrics.model_confidence:.2%}")
    print(f"Context nodes: {len(result.context_node_ids)}")
    print(f"Raw code nodes: {len(result.raw_code_nodes)}")
    print(f"Reason: {result.reason}")
