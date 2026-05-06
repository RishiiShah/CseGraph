from typing import Protocol

from graph.search import bfs
from graph.shortest_path import shortest_path_length


class TraversalEngine(Protocol):
    def shortest_path(self, graph: dict[str, list[str]], src: str, dst: str) -> int:
        """Return shortest path length from src to dst."""

    def reachable(self, graph: dict[str, list[str]], src: str, dst: str) -> bool:
        """Return true when dst is reachable from src."""


class DefaultTraversalEngine:
    def shortest_path(self, graph: dict[str, list[str]], src: str, dst: str) -> int:
        return shortest_path_length(graph, src, dst)

    def reachable(self, graph: dict[str, list[str]], src: str, dst: str) -> bool:
        return bfs(graph, src, dst) >= 0


class GraphQueryEvaluator:
    def __init__(self, engine: TraversalEngine | None = None):
        self.engine = engine or DefaultTraversalEngine()

    def evaluate(self, graph: dict[str, list[str]], src: str, dst: str) -> dict:
        shortest = self.engine.shortest_path(graph, src, dst)
        reachable_count = 0
        for node in graph:
            if self.engine.reachable(graph, src, node):
                reachable_count += 1

        return {
            "src": src,
            "dst": dst,
            "shortest_path": shortest,
            "reachable_nodes": reachable_count,
        }


def evaluate_query(graph: dict[str, list[str]], src: str, dst: str) -> dict:
    return GraphQueryEvaluator().evaluate(graph, src, dst)
