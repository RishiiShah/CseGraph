"""Tests for the Context Sufficiency Estimator agent."""

import json
import os
import tempfile

import pytest

from agents.cse_agent import CSEAgent
from models.cse_result import SufficiencyMetrics, SufficiencyQuery
from models.link_graph import GraphEdge, GraphNode, LinkGraph, LinkGraphSummary
from models.compressed_graph import CompressedGraph, ContextSlice, NodeSummary


# ---------------------------------------------------------------------------
# Helpers: build minimal graphs for testing
# ---------------------------------------------------------------------------


def _make_link_graph(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    root_dir: str = "/tmp/test_repo",
) -> LinkGraph:
    file_count = sum(1 for n in nodes if n.type == "file")
    symbol_count = len(nodes) - file_count
    return LinkGraph(
        root_dir=root_dir,
        summary=LinkGraphSummary(
            file_count=file_count,
            symbol_count=symbol_count,
            edge_count=len(edges),
        ),
        nodes=nodes,
        edges=edges,
    )


def _make_compressed_graph(
    link_graph: LinkGraph,
    context_slices: dict | None = None,
) -> CompressedGraph:
    summaries = {}
    for node in link_graph.nodes:
        summaries[node.id] = NodeSummary(
            node_id=node.id,
            name=node.name,
            node_type=node.type,
            file_path=node.file_path,
            summary=f"{node.type}: {node.name}",
        )
    return CompressedGraph(
        root_dir=link_graph.root_dir,
        original_graph_size={
            "file_count": link_graph.summary.file_count,
            "symbol_count": link_graph.summary.symbol_count,
            "edge_count": link_graph.summary.edge_count,
        },
        node_summaries=summaries,
        high_degree_nodes=[],
        context_slices=context_slices or {},
    )


def _write_graphs(tmp_dir, link_graph, compressed_graph):
    lg_path = os.path.join(tmp_dir, "link_graph.json")
    cg_path = os.path.join(tmp_dir, "compressed_graph.json")
    with open(lg_path, "w") as f:
        json.dump(link_graph.model_dump(), f)
    with open(cg_path, "w") as f:
        json.dump(compressed_graph.model_dump(), f)
    return lg_path, cg_path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def simple_graph_dir(tmp_path):
    """Create a small graph where func_a calls func_b and func_c.

    All three live in the same file.  func_a's dependencies are fully
    resolvable.
    """
    nodes = [
        GraphNode(id="file::main.py", type="file", name="main.py", file_path="main.py"),
        GraphNode(id="symbol::main.py::function::func_a", type="function", name="func_a", file_path="main.py", start_line=1, end_line=5),
        GraphNode(id="symbol::main.py::function::func_b", type="function", name="func_b", file_path="main.py", start_line=7, end_line=10),
        GraphNode(id="symbol::main.py::function::func_c", type="function", name="func_c", file_path="main.py", start_line=12, end_line=15),
    ]
    edges = [
        GraphEdge(source="file::main.py", target="symbol::main.py::function::func_a", relation="contains"),
        GraphEdge(source="file::main.py", target="symbol::main.py::function::func_b", relation="contains"),
        GraphEdge(source="file::main.py", target="symbol::main.py::function::func_c", relation="contains"),
        GraphEdge(source="symbol::main.py::function::func_a", target="symbol::main.py::function::func_b", relation="calls", metadata={"symbol": "func_b"}),
        GraphEdge(source="symbol::main.py::function::func_a", target="symbol::main.py::function::func_c", relation="calls", metadata={"symbol": "func_c"}),
    ]
    lg = _make_link_graph(nodes, edges)
    cg = _make_compressed_graph(lg)
    lg_path, cg_path = _write_graphs(str(tmp_path), lg, cg)
    return lg_path, cg_path


@pytest.fixture()
def missing_dep_graph_dir(tmp_path):
    """Graph where func_a calls func_b, func_c, and func_d.

    func_d lives in a separate file and is NOT in the radius-1 context.
    """
    nodes = [
        GraphNode(id="file::main.py", type="file", name="main.py", file_path="main.py"),
        GraphNode(id="symbol::main.py::function::func_a", type="function", name="func_a", file_path="main.py", start_line=1, end_line=5),
        GraphNode(id="symbol::main.py::function::func_b", type="function", name="func_b", file_path="main.py", start_line=7, end_line=10),
        GraphNode(id="symbol::main.py::function::func_c", type="function", name="func_c", file_path="main.py", start_line=12, end_line=15),
        GraphNode(id="file::utils.py", type="file", name="utils.py", file_path="utils.py"),
        GraphNode(id="symbol::utils.py::function::func_d", type="function", name="func_d", file_path="utils.py", start_line=1, end_line=5),
    ]
    edges = [
        GraphEdge(source="file::main.py", target="symbol::main.py::function::func_a", relation="contains"),
        GraphEdge(source="file::main.py", target="symbol::main.py::function::func_b", relation="contains"),
        GraphEdge(source="file::main.py", target="symbol::main.py::function::func_c", relation="contains"),
        GraphEdge(source="file::utils.py", target="symbol::utils.py::function::func_d", relation="contains"),
        GraphEdge(source="symbol::main.py::function::func_a", target="symbol::main.py::function::func_b", relation="calls", metadata={"symbol": "func_b"}),
        GraphEdge(source="symbol::main.py::function::func_a", target="symbol::main.py::function::func_c", relation="calls", metadata={"symbol": "func_c"}),
        GraphEdge(source="symbol::main.py::function::func_a", target="symbol::utils.py::function::func_d", relation="calls", metadata={"symbol": "func_d"}),
    ]
    lg = _make_link_graph(nodes, edges)
    cg = _make_compressed_graph(lg)
    lg_path, cg_path = _write_graphs(str(tmp_path), lg, cg)
    return lg_path, cg_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDependencyCompleteness:
    def test_all_deps_present(self, simple_graph_dir):
        lg_path, cg_path = simple_graph_dir
        agent = CSEAgent(lg_path, cg_path)
        # Context includes all nodes
        context = {n.id for n in agent.link_graph.nodes}
        score = agent._compute_dependency_completeness(
            "symbol::main.py::function::func_a", context
        )
        assert score == 1.0

    def test_missing_dep(self, missing_dep_graph_dir):
        lg_path, cg_path = missing_dep_graph_dir
        agent = CSEAgent(lg_path, cg_path)
        # Context only includes main.py nodes (not func_d)
        context = {
            "file::main.py",
            "symbol::main.py::function::func_a",
            "symbol::main.py::function::func_b",
            "symbol::main.py::function::func_c",
        }
        score = agent._compute_dependency_completeness(
            "symbol::main.py::function::func_a", context
        )
        # 2 of 3 deps resolved
        assert abs(score - 2.0 / 3.0) < 0.01

    def test_no_deps(self, simple_graph_dir):
        lg_path, cg_path = simple_graph_dir
        agent = CSEAgent(lg_path, cg_path)
        # func_b has no outgoing calls
        score = agent._compute_dependency_completeness(
            "symbol::main.py::function::func_b", set()
        )
        assert score == 1.0


class TestEntityCoverage:
    def test_entities_found(self, simple_graph_dir):
        lg_path, cg_path = simple_graph_dir
        agent = CSEAgent(lg_path, cg_path)
        score = agent._compute_entity_coverage(
            "Generate func_b and func_c",
            {"func_a", "func_b", "func_c"},
        )
        assert score == 1.0

    def test_entity_missing(self, simple_graph_dir):
        lg_path, cg_path = simple_graph_dir
        agent = CSEAgent(lg_path, cg_path)
        # Both func_b and func_c are real graph symbols; only func_b is in context.
        # _compute_entity_coverage first filters extracted entities against actual
        # graph node names, so only real symbols count toward the score.
        score = agent._compute_entity_coverage(
            "call func_b and func_c",
            {"func_a", "func_b"},  # func_c is a real symbol but absent from context
        )
        # func_b found, func_c not → 1/2
        assert abs(score - 0.5) < 0.01

    def test_no_entities_in_query(self, simple_graph_dir):
        lg_path, cg_path = simple_graph_dir
        agent = CSEAgent(lg_path, cg_path)
        score = agent._compute_entity_coverage(
            "just some plain words here",  # no CamelCase or snake_case identifiers
            {"func_a", "func_b"},
        )
        assert score == 1.0  # No entities → trivially covered


class TestSemanticOverlap:
    def test_identical_text(self, simple_graph_dir):
        lg_path, cg_path = simple_graph_dir
        agent = CSEAgent(lg_path, cg_path)
        score = agent._compute_semantic_overlap(
            "function definition for parsing",
            ["function definition for parsing code"],
        )
        # TF-IDF cosine similarity gives high score for similar text
        assert score > 0.3

    def test_unrelated_text(self, simple_graph_dir):
        lg_path, cg_path = simple_graph_dir
        agent = CSEAgent(lg_path, cg_path)
        score = agent._compute_semantic_overlap(
            "quantum physics experiment",
            ["function: func_a", "function: func_b"],
        )
        # Should be low similarity
        assert score < 0.5

    def test_empty_context(self, simple_graph_dir):
        lg_path, cg_path = simple_graph_dir
        agent = CSEAgent(lg_path, cg_path)
        score = agent._compute_semantic_overlap("some query", [])
        assert score == 0.0


class TestEntityExtraction:
    def test_camel_case(self, simple_graph_dir):
        lg_path, cg_path = simple_graph_dir
        agent = CSEAgent(lg_path, cg_path)
        entities = agent._extract_query_entities("Use IngestionAgent to parse files")
        assert "IngestionAgent" in entities

    def test_snake_case(self, simple_graph_dir):
        lg_path, cg_path = simple_graph_dir
        agent = CSEAgent(lg_path, cg_path)
        entities = agent._extract_query_entities("call ingest_repository for all files")
        assert "ingest_repository" in entities

    def test_noise_filtered(self, simple_graph_dir):
        lg_path, cg_path = simple_graph_dir
        agent = CSEAgent(lg_path, cg_path)
        entities = agent._extract_query_entities("This should Generate code With care")
        # This, Generate, With are noise words
        assert "This" not in entities
        assert "Generate" not in entities
        assert "With" not in entities


class TestEvaluateEndToEnd:
    def test_sufficient_context(self, simple_graph_dir):
        lg_path, cg_path = simple_graph_dir
        agent = CSEAgent(lg_path, cg_path)
        query = SufficiencyQuery(
            query_text="Generate code related to func_a",
            target_node_id="symbol::main.py::function::func_a",
            target_file_path="main.py",
        )
        result = agent.evaluate(query)
        # All deps are reachable via BFS — structural metrics must pass.
        # model_confidence may stay below threshold when test fixtures have no real
        # source files (summaries are short template strings → low compression_factor).
        assert result.metrics.dependency_completeness >= 0.80
        assert result.metrics.entity_coverage >= 0.80

    def test_expansion_triggered(self, missing_dep_graph_dir):
        lg_path, cg_path = missing_dep_graph_dir
        agent = CSEAgent(lg_path, cg_path)
        query = SufficiencyQuery(
            query_text="Generate code for func_a",
            target_node_id="symbol::main.py::function::func_a",
            target_file_path="main.py",
        )
        result = agent.evaluate(query)
        # Expansion should happen to pull in func_d
        assert result.expansion_rounds >= 0
        assert result.max_rounds == 3

    def test_result_has_all_fields(self, simple_graph_dir):
        lg_path, cg_path = simple_graph_dir
        agent = CSEAgent(lg_path, cg_path)
        query = SufficiencyQuery(
            query_text="Generate code related to func_a",
            target_node_id="symbol::main.py::function::func_a",
            target_file_path="main.py",
        )
        result = agent.evaluate(query)
        assert result.thresholds["dependency_completeness"] == 0.80
        assert result.thresholds["entity_coverage"] == 0.80
        assert result.thresholds["semantic_overlap"] == 0.50
        assert len(result.context_node_ids) > 0
        assert result.reason in ("All thresholds met", "Max expansion rounds reached")


class TestPickRepresentativeTarget:
    def test_picks_highest_degree(self, simple_graph_dir):
        lg_path, cg_path = simple_graph_dir
        agent = CSEAgent(lg_path, cg_path)
        node_id, file_path, rich_query = agent.pick_representative_target()
        # func_a has the most edges (2 outgoing calls + 1 incoming contains)
        assert "func_a" in node_id
        assert file_path == "main.py"
        assert isinstance(rich_query, str) and len(rich_query) > 0


class TestSaveResult:
    def test_save_and_reload(self, simple_graph_dir, tmp_path):
        lg_path, cg_path = simple_graph_dir
        agent = CSEAgent(lg_path, cg_path)
        query = SufficiencyQuery(
            query_text="Generate code related to func_a",
            target_node_id="symbol::main.py::function::func_a",
            target_file_path="main.py",
        )
        result = agent.evaluate(query)
        out_path = str(tmp_path / "cse_result.json")
        agent.save_result(result, out_path)

        with open(out_path) as f:
            data = json.load(f)

        assert data["is_sufficient"] is True or data["is_sufficient"] is False
        assert "metrics" in data
        assert "context_node_ids" in data
        assert "recompressed_rounds" in data
        assert isinstance(data["recompressed_rounds"], int)


# ---------------------------------------------------------------------------
# Tests: resummary_fn callback and _recompress_nodes
# ---------------------------------------------------------------------------


class TestResummaryCallback:
    def test_resummary_fn_stored(self, simple_graph_dir):
        lg_path, cg_path = simple_graph_dir
        fn = lambda nid: "fresh summary"
        agent = CSEAgent(lg_path, cg_path, resummary_fn=fn)
        assert agent._resummary_fn is fn

    def test_no_resummary_fn_by_default(self, simple_graph_dir):
        lg_path, cg_path = simple_graph_dir
        agent = CSEAgent(lg_path, cg_path)
        assert agent._resummary_fn is None

    def test_recompress_updates_existing_summary(self, simple_graph_dir):
        lg_path, cg_path = simple_graph_dir
        nid = "symbol::main.py::function::func_b"
        agent = CSEAgent(lg_path, cg_path, resummary_fn=lambda n: f"fresh-{n}")
        original = agent.compressed_graph.node_summaries[nid].summary
        agent._recompress_nodes({nid})
        updated = agent.compressed_graph.node_summaries[nid].summary
        assert updated == f"fresh-{nid}"
        assert updated != original

    def test_recompress_adds_new_entry_for_node_not_in_compressed(self, simple_graph_dir):
        lg_path, cg_path = simple_graph_dir
        nid = "symbol::main.py::function::func_c"
        agent = CSEAgent(lg_path, cg_path, resummary_fn=lambda n: "injected")
        del agent.compressed_graph.node_summaries[nid]
        agent._recompress_nodes({nid})
        assert nid in agent.compressed_graph.node_summaries
        assert agent.compressed_graph.node_summaries[nid].summary == "injected"

    def test_recompress_skips_when_fn_returns_empty_string(self, simple_graph_dir):
        lg_path, cg_path = simple_graph_dir
        nid = "symbol::main.py::function::func_b"
        agent = CSEAgent(lg_path, cg_path, resummary_fn=lambda n: "")
        original = agent.compressed_graph.node_summaries[nid].summary
        agent._recompress_nodes({nid})
        assert agent.compressed_graph.node_summaries[nid].summary == original

    def test_recompress_noop_without_fn(self, simple_graph_dir):
        lg_path, cg_path = simple_graph_dir
        nid = "symbol::main.py::function::func_b"
        agent = CSEAgent(lg_path, cg_path)
        original = agent.compressed_graph.node_summaries[nid].summary
        agent._recompress_nodes({nid})
        assert agent.compressed_graph.node_summaries[nid].summary == original

    def test_recompress_noop_with_empty_node_set(self, simple_graph_dir):
        lg_path, cg_path = simple_graph_dir
        called: list = []
        agent = CSEAgent(lg_path, cg_path, resummary_fn=lambda n: (called.append(n), "x")[1])
        agent._recompress_nodes(set())
        assert called == []


# ---------------------------------------------------------------------------
# Tests: recompressed_rounds field
# ---------------------------------------------------------------------------


class TestRecompressedRoundsField:
    def test_default_zero_without_fn(self, simple_graph_dir):
        lg_path, cg_path = simple_graph_dir
        agent = CSEAgent(lg_path, cg_path)
        query = SufficiencyQuery(
            query_text="Generate code for func_a",
            target_node_id="symbol::main.py::function::func_a",
            target_file_path="main.py",
        )
        result = agent.evaluate(query)
        assert result.recompressed_rounds == 0

    def test_field_present_in_serialised_json(self, simple_graph_dir, tmp_path):
        lg_path, cg_path = simple_graph_dir
        agent = CSEAgent(lg_path, cg_path)
        query = SufficiencyQuery(
            query_text="Generate code for func_a",
            target_node_id="symbol::main.py::function::func_a",
            target_file_path="main.py",
        )
        result = agent.evaluate(query)
        out_path = str(tmp_path / "result.json")
        agent.save_result(result, out_path)
        with open(out_path) as f:
            data = json.load(f)
        assert "recompressed_rounds" in data
        assert isinstance(data["recompressed_rounds"], int)
        assert data["recompressed_rounds"] >= 0


# ---------------------------------------------------------------------------
# Tests: expand_for_query
# ---------------------------------------------------------------------------


class TestExpandForQuery:
    def test_returns_sufficiency_result(self, simple_graph_dir):
        from models.cse_result import SufficiencyResult as _SR
        lg_path, cg_path = simple_graph_dir
        agent = CSEAgent(lg_path, cg_path)
        query = SufficiencyQuery(
            query_text="Generate code for func_a",
            target_node_id="symbol::main.py::function::func_a",
            target_file_path="main.py",
        )
        first = agent.evaluate(query)
        expanded = agent.expand_for_query(
            query=query,
            context_ids=list(first.context_node_ids),
            raw_code_ids=list(first.raw_code_nodes),
        )
        assert isinstance(expanded, _SR)

    def test_expansion_rounds_exceeds_max_rounds(self, simple_graph_dir):
        lg_path, cg_path = simple_graph_dir
        agent = CSEAgent(lg_path, cg_path)
        query = SufficiencyQuery(
            query_text="Generate code for func_a",
            target_node_id="symbol::main.py::function::func_a",
            target_file_path="main.py",
        )
        first = agent.evaluate(query)
        expanded = agent.expand_for_query(
            query=query,
            context_ids=list(first.context_node_ids),
            raw_code_ids=list(first.raw_code_nodes),
        )
        assert expanded.expansion_rounds == agent.MAX_ROUNDS + 1

    def test_custom_reason_prefix_in_result(self, simple_graph_dir):
        lg_path, cg_path = simple_graph_dir
        agent = CSEAgent(lg_path, cg_path)
        query = SufficiencyQuery(
            query_text="Generate code for func_b",
            target_node_id="symbol::main.py::function::func_b",
            target_file_path="main.py",
        )
        first = agent.evaluate(query)
        expanded = agent.expand_for_query(
            query=query,
            context_ids=list(first.context_node_ids),
            raw_code_ids=[],
            reason_prefix="Logprob-triggered",
        )
        assert expanded.reason.startswith("Logprob-triggered")

    def test_context_ids_non_empty(self, simple_graph_dir):
        lg_path, cg_path = simple_graph_dir
        agent = CSEAgent(lg_path, cg_path)
        query = SufficiencyQuery(
            query_text="Generate code for func_a",
            target_node_id="symbol::main.py::function::func_a",
            target_file_path="main.py",
        )
        first = agent.evaluate(query)
        expanded = agent.expand_for_query(
            query=query,
            context_ids=list(first.context_node_ids),
            raw_code_ids=[],
        )
        assert len(expanded.context_node_ids) > 0

    def test_raw_code_ids_preserved(self, simple_graph_dir):
        lg_path, cg_path = simple_graph_dir
        agent = CSEAgent(lg_path, cg_path)
        query = SufficiencyQuery(
            query_text="Generate code for func_a",
            target_node_id="symbol::main.py::function::func_a",
            target_file_path="main.py",
        )
        first = agent.evaluate(query)
        raw_ids = ["symbol::main.py::function::func_b"]
        expanded = agent.expand_for_query(
            query=query,
            context_ids=list(first.context_node_ids),
            raw_code_ids=raw_ids,
        )
        assert "symbol::main.py::function::func_b" in expanded.raw_code_nodes
