import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from agents.compression_agent import CompressionAgent
from models.compressed_graph import CompressedGraph
from models.link_graph import GraphEdge, GraphNode, LinkGraph, LinkGraphSummary


class TestCompressionAgent(unittest.TestCase):
    def setUp(self) -> None:
        self.test_dir = Path(__file__).parent / "fixtures" / "sandboxes" / "baseline_import_resolution"
        self.graph_path = self.test_dir / "test_link_graph.json"

    def _create_mock_graph(self) -> LinkGraph:
        """Create a mock LinkGraph for testing."""
        nodes = [
            GraphNode(
                id="file::main.py",
                type="file",
                name="main.py",
                file_path="main.py",
            ),
            GraphNode(
                id="symbol::main.py::function::process",
                type="function",
                name="process",
                file_path="main.py",
                start_line=5,
                end_line=15,
            ),
            GraphNode(
                id="symbol::main.py::class::Handler",
                type="class",
                name="Handler",
                file_path="main.py",
                start_line=18,
                end_line=40,
            ),
            GraphNode(
                id="symbol::main.py::method::Handler.run",
                type="method",
                name="Handler.run",
                file_path="main.py",
                start_line=20,
                end_line=35,
            ),
            GraphNode(
                id="file::utils.py",
                type="file",
                name="utils.py",
                file_path="utils.py",
            ),
            GraphNode(
                id="symbol::utils.py::function::helper",
                type="function",
                name="helper",
                file_path="utils.py",
                start_line=3,
                end_line=10,
            ),
        ]

        edges = [
            GraphEdge(
                source="file::main.py",
                target="symbol::main.py::function::process",
                relation="contains",
            ),
            GraphEdge(
                source="file::main.py",
                target="symbol::main.py::class::Handler",
                relation="contains",
            ),
            GraphEdge(
                source="symbol::main.py::class::Handler",
                target="symbol::main.py::method::Handler.run",
                relation="contains",
            ),
            GraphEdge(
                source="symbol::main.py::function::process",
                target="symbol::utils.py::function::helper",
                relation="calls",
            ),
            GraphEdge(
                source="file::main.py",
                target="file::utils.py",
                relation="imports",
            ),
            GraphEdge(
                source="symbol::main.py::class::Handler",
                target="symbol::utils.py::function::helper",
                relation="calls",
            ),
        ]

        return LinkGraph(
            root_dir=str(self.test_dir),
            summary=LinkGraphSummary(
                file_count=2, symbol_count=4, edge_count=len(edges)
            ),
            nodes=nodes,
            edges=edges,
        )

    def _save_mock_graph(self, graph: LinkGraph, output_path: Path) -> None:
        """Save mock graph to JSON."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        data = graph.model_dump()
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

    def test_compression_agent_initializes(self) -> None:
        """Test that compression agent can be initialized with a graph."""
        graph = self._create_mock_graph()
        self._save_mock_graph(graph, self.graph_path)

        agent = CompressionAgent(str(self.graph_path))

        self.assertEqual(agent.root_dir, str(self.test_dir))
        self.assertEqual(len(agent._node_lookup), 6)
        # 3 nodes have outgoing edges: file::main.py, process function, Handler class
        self.assertEqual(len(agent._outgoing), 3)
        # 5 nodes have incoming edges
        self.assertEqual(len(agent._incoming), 5)

    def test_generate_node_summary(self) -> None:
        """Test that node summaries are generated for all node types."""
        graph = self._create_mock_graph()
        self._save_mock_graph(graph, self.graph_path)

        agent = CompressionAgent(str(self.graph_path))

        # Test file node summary — new format: "Module <name> defines: ..."
        file_summary = agent._generate_node_summary("file::main.py")
        self.assertIn("main.py", file_summary)
        self.assertIn("defines", file_summary)

        # Test class node summary — new format: signature line or "class <name>"
        class_summary = agent._generate_node_summary("symbol::main.py::class::Handler")
        self.assertIn("Handler", class_summary)

        # Test function node summary — new format: signature line or "def <name>"
        func_summary = agent._generate_node_summary("symbol::main.py::function::process")
        self.assertIn("process", func_summary)

        # Test unknown node
        unknown_summary = agent._generate_node_summary("nonexistent")
        self.assertEqual(unknown_summary, "Unknown node")

    def test_compute_high_degree_nodes(self) -> None:
        """Test identification of high-degree hub nodes."""
        graph = self._create_mock_graph()
        self._save_mock_graph(graph, self.graph_path)

        agent = CompressionAgent(str(self.graph_path))
        high_degree = agent._compute_high_degree_nodes(top_k=3)

        # Should have at most 3 nodes
        self.assertLessEqual(len(high_degree), 3)

        # High-degree nodes should be in the graph
        for node_id in high_degree:
            self.assertIn(node_id, agent._node_lookup)

    def test_get_neighborhood(self) -> None:
        """Test neighborhood extraction at different radii."""
        graph = self._create_mock_graph()
        self._save_mock_graph(graph, self.graph_path)

        agent = CompressionAgent(str(self.graph_path))

        # Test radius 1 neighborhood
        neighborhood_r1, edges_r1 = agent._get_neighborhood(
            "file::main.py", radius=1, max_nodes=50
        )

        self.assertGreater(len(neighborhood_r1), 0)
        self.assertIn("file::main.py", neighborhood_r1)

        # Verify edge types are tracked
        self.assertGreater(len(edges_r1), 0)
        self.assertTrue(any(e in edges_r1 for e in ["contains", "imports", "calls"]))

        # Test radius 2 neighborhood (should be larger)
        neighborhood_r2, edges_r2 = agent._get_neighborhood(
            "file::main.py", radius=2, max_nodes=50
        )

        self.assertGreaterEqual(len(neighborhood_r2), len(neighborhood_r1))

    def test_estimate_compression_ratio(self) -> None:
        """Test compression ratio estimation."""
        graph = self._create_mock_graph()
        self._save_mock_graph(graph, self.graph_path)

        agent = CompressionAgent(str(self.graph_path))

        # Test valid compression ratio
        ratio = agent._estimate_compression_ratio(context_size=10, original_size=100)
        self.assertGreaterEqual(ratio, 0.0)
        self.assertLessEqual(ratio, 1.0)

        # Zero original size should give 0
        ratio_zero = agent._estimate_compression_ratio(context_size=10, original_size=0)
        self.assertEqual(ratio_zero, 0.0)

    def test_compress(self) -> None:
        """Test full compression pipeline."""
        graph = self._create_mock_graph()
        self._save_mock_graph(graph, self.graph_path)

        agent = CompressionAgent(str(self.graph_path))
        compressed = agent.compress()

        # Verify compressed graph structure
        self.assertIsInstance(compressed, CompressedGraph)
        self.assertEqual(
            compressed.original_graph_size["file_count"], 2
        )
        self.assertEqual(
            compressed.original_graph_size["symbol_count"], 4
        )
        self.assertEqual(
            compressed.original_graph_size["edge_count"], 6
        )

        # Verify node summaries
        self.assertGreater(len(compressed.node_summaries), 0)
        self.assertGreaterEqual(len(compressed.node_summaries), 4)

        # Verify high-degree nodes are identified
        self.assertGreater(len(compressed.high_degree_nodes), 0)

        # Verify context slices are generated
        self.assertGreater(len(compressed.context_slices), 0)

        # Verify compression stats
        self.assertIn("avg_compression_ratio", compressed.compression_stats)
        self.assertIn("max_compression_ratio", compressed.compression_stats)
        self.assertGreaterEqual(
            compressed.compression_stats["avg_compression_ratio"], 0.0
        )
        self.assertLessEqual(
            compressed.compression_stats["avg_compression_ratio"], 1.0
        )

    def test_save_compressed(self) -> None:
        """Test saving compressed graph to JSON."""
        graph = self._create_mock_graph()
        self._save_mock_graph(graph, self.graph_path)

        output_path = self.graph_path.parent / "compressed_test.json"

        try:
            agent = CompressionAgent(str(self.graph_path))
            compressed = agent.compress()
            agent.save_compressed(compressed, str(output_path))

            # Verify file was created
            self.assertTrue(output_path.exists())

            # Verify JSON is valid and can be loaded
            with open(output_path, "r", encoding="utf-8") as f:
                loaded_data = json.load(f)

            # Verify structure
            self.assertIn("node_summaries", loaded_data)
            self.assertIn("high_degree_nodes", loaded_data)
            self.assertIn("context_slices", loaded_data)
            self.assertIn("compression_stats", loaded_data)

        finally:
            if output_path.exists():
                output_path.unlink()

    def test_node_summaries_have_required_fields(self) -> None:
        """Test that all node summaries contain required fields."""
        graph = self._create_mock_graph()
        self._save_mock_graph(graph, self.graph_path)

        agent = CompressionAgent(str(self.graph_path))
        compressed = agent.compress()

        for node_id, summary in compressed.node_summaries.items():
            self.assertEqual(summary.node_id, node_id)
            self.assertIsNotNone(summary.name)
            self.assertIsNotNone(summary.node_type)
            self.assertIsNotNone(summary.file_path)
            self.assertIsNotNone(summary.summary)
            self.assertIsInstance(summary.key_dependencies, list)
            self.assertIsInstance(summary.dependents, list)

    def test_context_slices_consistency(self) -> None:
        """Test that context slices are internally consistent."""
        graph = self._create_mock_graph()
        self._save_mock_graph(graph, self.graph_path)

        agent = CompressionAgent(str(self.graph_path))
        compressed = agent.compress()

        for slice_key, context_slice in compressed.context_slices.items():
            # Verify anchor node is in included nodes
            self.assertIn(
                context_slice.anchor_node_id, context_slice.included_nodes
            )

            # Verify compression ratio is valid
            self.assertGreaterEqual(context_slice.compressed_size_ratio, 0.0)
            self.assertLessEqual(context_slice.compressed_size_ratio, 1.0)

            # Verify all included nodes have summaries
            for node_id, summary in context_slice.included_nodes.items():
                self.assertEqual(summary.node_id, node_id)

    def test_integration_with_real_fixture(self) -> None:
        """Test compression with actual fixture repository if available."""
        fixture_graph = Path(__file__).parent / "fixtures" / "sandboxes" / "baseline_import_resolution" / "link_graph.json"
        
        if not fixture_graph.exists():
            self.skipTest("Fixture link graph not available")

        agent = CompressionAgent(str(fixture_graph))
        compressed = agent.compress()

        # Verify successful compression
        self.assertGreater(len(compressed.node_summaries), 0)
        self.assertGreater(len(compressed.context_slices), 0)
        self.assertGreater(
            compressed.compression_stats["avg_compression_ratio"], 0.0
        )


class TestUseLlmFallback(unittest.TestCase):
    """Tests for the use_llm parameter and graceful fallback behaviour."""

    def setUp(self) -> None:
        """Create a temporary link_graph.json for use across tests in this class."""
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._graph_path = Path(self._tmp_dir.name) / "link_graph.json"
        self._save_mock_graph(self._create_mock_graph(), self._graph_path)

    def tearDown(self) -> None:
        self._tmp_dir.cleanup()

    # ------------------------------------------------------------------
    # Helpers reused from the parent class
    # ------------------------------------------------------------------

    def _create_mock_graph(self) -> LinkGraph:
        nodes = [
            GraphNode(
                id="file::main.py",
                type="file",
                name="main.py",
                file_path="main.py",
            ),
            GraphNode(
                id="symbol::main.py::function::process",
                type="function",
                name="process",
                file_path="main.py",
                start_line=1,
                end_line=5,
            ),
            GraphNode(
                id="file::utils.py",
                type="file",
                name="utils.py",
                file_path="utils.py",
            ),
            GraphNode(
                id="symbol::utils.py::function::helper",
                type="function",
                name="helper",
                file_path="utils.py",
                start_line=1,
                end_line=4,
            ),
        ]
        edges = [
            GraphEdge(
                source="file::main.py",
                target="symbol::main.py::function::process",
                relation="contains",
            ),
            GraphEdge(
                source="file::utils.py",
                target="symbol::utils.py::function::helper",
                relation="contains",
            ),
            GraphEdge(
                source="symbol::main.py::function::process",
                target="symbol::utils.py::function::helper",
                relation="calls",
            ),
        ]
        return LinkGraph(
            root_dir=self._tmp_dir.name,
            summary=LinkGraphSummary(
                file_count=2, symbol_count=2, edge_count=len(edges)
            ),
            nodes=nodes,
            edges=edges,
        )

    def _save_mock_graph(self, graph: LinkGraph, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        data = graph.model_dump()
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

    # ------------------------------------------------------------------
    # Test 1: default (use_llm=False) — AST path produces non-empty summaries
    # ------------------------------------------------------------------

    def test_use_llm_false_default(self) -> None:
        """CompressionAgent with no use_llm arg uses AST path and produces summaries."""
        agent = CompressionAgent(str(self._graph_path))
        compressed = agent.compress()

        self.assertGreater(len(compressed.node_summaries), 0)
        for node_id, summary in compressed.node_summaries.items():
            self.assertIsInstance(summary.summary, str)
            self.assertGreater(
                len(summary.summary), 0,
                f"Summary for {node_id!r} is an empty string",
            )

    # ------------------------------------------------------------------
    # Test 2: use_llm=True with no model available → falls back to AST
    # ------------------------------------------------------------------

    def test_use_llm_true_no_key(self) -> None:
        """use_llm=True with no GGUF model and empty Groq key still produces summaries.

        When no LLM backend is available, CompressionAgent falls back to the
        AST-based path, so compress() must still produce non-empty summaries.
        """
        agent = CompressionAgent(
            str(self._graph_path),
            use_llm=True,
            groq_api_key="",
        )

        # Compression must succeed and produce non-empty summaries regardless
        # of whether the Groq client could make a real API call.
        compressed = agent.compress()
        self.assertGreater(len(compressed.node_summaries), 0)
        for node_id, summary in compressed.node_summaries.items():
            self.assertIsInstance(summary.summary, str)
            self.assertGreater(
                len(summary.summary), 0,
                f"Fallback summary for {node_id!r} is an empty string",
            )

    # ------------------------------------------------------------------
    # Test 3: CodeGenResult has mean_logprob field
    # ------------------------------------------------------------------

    def test_mean_logprob_field_exists(self) -> None:
        """CodeGenResult model must declare a mean_logprob field."""
        from models.code_gen_result import CodeGenResult

        self.assertIn(
            "mean_logprob",
            CodeGenResult.model_fields,
            "CodeGenResult is missing the 'mean_logprob' field",
        )


if __name__ == "__main__":
    unittest.main()
