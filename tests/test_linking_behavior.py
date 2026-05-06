import os
import sys
import unittest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from agents.linking_agent import LinkingAgent


class TestLinkingBehavior(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture_root = os.path.join(
            PROJECT_ROOT,
            "sandboxes",
            "baseline_import_resolution",
        )
        cls.agent = LinkingAgent(cls.fixture_root)
        cls.graph = cls.agent.build_graph()

    def _find_edge(self, source: str, target: str, relation: str) -> bool:
        return any(
            edge.source == source and edge.target == target and edge.relation == relation
            for edge in self.graph.edges
        )

    def test_relative_import_resolves_to_local_file(self) -> None:
        self.assertTrue(
            self._find_edge(
                source="file::pkg/service.py",
                target="file::pkg/utils.py",
                relation="imports",
            )
        )

    def test_same_file_symbol_preferred_for_call_resolution(self) -> None:
        self.assertTrue(
            self._find_edge(
                source="symbol::pkg/utils.py::function::caller",
                target="symbol::pkg/utils.py::function::process",
                relation="calls",
            )
        )

    def test_graph_build_is_deterministic(self) -> None:
        second_graph = self.agent.build_graph()
        self.assertEqual(self.graph.model_dump(), second_graph.model_dump())


if __name__ == "__main__":
    unittest.main()
