import os
import sys
import unittest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from agents.linking_agent import LinkingAgent


class TestLinkGraphIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.agent = LinkingAgent(PROJECT_ROOT)
        cls.graph = cls.agent.build_graph()
        cls.node_by_id = {node.id: node for node in cls.graph.nodes}

    def test_graph_summary_counts_match_payload(self) -> None:
        file_count = sum(1 for node in self.graph.nodes if node.type == "file")
        symbol_count = sum(1 for node in self.graph.nodes if node.type != "file")

        self.assertEqual(file_count, self.graph.summary.file_count)
        self.assertEqual(symbol_count, self.graph.summary.symbol_count)
        self.assertEqual(len(self.graph.edges), self.graph.summary.edge_count)

    def test_all_edges_reference_existing_nodes(self) -> None:
        for edge in self.graph.edges:
            self.assertIn(edge.source, self.node_by_id)
            self.assertIn(edge.target, self.node_by_id)

    def test_import_edges_target_file_nodes(self) -> None:
        for edge in self.graph.edges:
            if edge.relation != "imports":
                continue
            self.assertEqual(self.node_by_id[edge.target].type, "file")

    def test_calls_edges_are_not_self_references(self) -> None:
        for edge in self.graph.edges:
            if edge.relation != "calls":
                continue
            self.assertNotEqual(edge.source, edge.target)

    def test_contains_edge_shapes(self) -> None:
        for edge in self.graph.edges:
            if edge.relation != "contains":
                continue
            source_type = self.node_by_id[edge.source].type
            target_type = self.node_by_id[edge.target].type
            is_file_to_symbol = source_type == "file" and target_type in {
                "function",
                "class",
            }
            is_class_to_method = source_type == "class" and target_type == "method"
            self.assertTrue(is_file_to_symbol or is_class_to_method)


if __name__ == "__main__":
    unittest.main()
