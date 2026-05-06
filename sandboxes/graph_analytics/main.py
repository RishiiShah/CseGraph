from metrics.evaluator import evaluate_query


def run_case() -> list[dict]:
    graph = {
        "A": ["B", "C"],
        "B": ["D"],
        "C": ["D"],
        "D": [],
    }
    return [
        evaluate_query(graph, "A", "D"),
        evaluate_query(graph, "B", "C"),
    ]
