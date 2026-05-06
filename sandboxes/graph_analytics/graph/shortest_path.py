from .search import bfs


def shortest_path_length(graph: dict[str, list[str]], start: str, target: str) -> int:
    return bfs(graph, start, target)
