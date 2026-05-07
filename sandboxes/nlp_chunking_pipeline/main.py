from pipeline.orchestrator import run_demo


def run_pipeline_case() -> dict:
    text = "Graph retrieval improves code generation by surfacing relevant symbols quickly."
    query = "retrieval code symbols"
    return run_demo(text, query)
