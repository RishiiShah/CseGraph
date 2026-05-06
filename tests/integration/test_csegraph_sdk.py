import sqlite3
from pathlib import Path

from csegraph import (
    ContextService,
    GraphQueryService,
    IndexService,
    ProjectIndex,
    RefreshService,
)


def _write_sample_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "utils.py").write_text(
        "\n".join(
            [
                "def format_user(name: str) -> str:",
                "    \"\"\"Normalize a display name.\"\"\"",
                "    return name.strip().title()",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (root / "main.py").write_text(
        "\n".join(
            [
                "from utils import format_user",
                "",
                "def build_report(name: str) -> str:",
                "    \"\"\"Build a simple user report.\"\"\"",
                "    return f'Report: {format_user(name)}'",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_project_index_schema_is_idempotent(tmp_path):
    db_path = tmp_path / "index.db"

    index = ProjectIndex(db_path)
    index.initialize_schema()
    index.initialize_schema()
    index.close()

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table')"
            )
        }

    assert "schema_meta" in tables
    assert "projects" in tables
    assert "files" in tables
    assert "symbols" in tables
    assert "edges" in tables
    assert "summaries" in tables
    assert "lexical_index" in tables
    assert "embedding_cache" in tables
    assert "retrieval_runs" in tables
    assert "retrieval_context" in tables


def test_index_context_graph_and_incremental_refresh(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "repo.csegraph.db"
    _write_sample_repo(repo)

    index_result = IndexService(db_path).index(repo, profile="small")

    assert index_result.files_indexed == 2
    assert index_result.symbols_indexed == 2
    assert index_result.edges_indexed >= 3
    assert index_result.profile == "small"

    context = ContextService(db_path).build_context(
        task="Implement build_report using format_user",
        target="symbol::main.py::function::build_report",
        profile="small",
    )

    context_ids = {node.node_id for node in context.context_nodes}
    assert context.target_node_id == "symbol::main.py::function::build_report"
    assert "symbol::main.py::function::build_report" in context_ids
    assert "symbol::utils.py::function::format_user" in context_ids
    assert context.metrics.dependency_completeness == 1.0
    assert context.metrics.entity_coverage == 1.0
    assert context.is_sufficient is True

    graph = GraphQueryService(db_path).neighborhood(
        "symbol::main.py::function::build_report",
        depth=1,
    )
    edge_shapes = {(edge.source, edge.relation, edge.target) for edge in graph.edges}
    assert (
        "symbol::main.py::function::build_report",
        "calls",
        "symbol::utils.py::function::format_user",
    ) in edge_shapes

    no_change = RefreshService(db_path).refresh(profile="small")
    assert no_change.changed_files == []
    assert no_change.deleted_files == []
    assert no_change.files_indexed == 0

    (repo / "utils.py").write_text(
        "\n".join(
            [
                "def format_user(name: str) -> str:",
                "    \"\"\"Normalize a display name.\"\"\"",
                "    return name.strip().title()",
                "",
                "def format_title(title: str) -> str:",
                "    return title.strip().upper()",
                "",
            ]
        ),
        encoding="utf-8",
    )

    refreshed = RefreshService(db_path).refresh(profile="small")
    assert refreshed.changed_files == ["utils.py"]
    assert refreshed.files_indexed == 1
    assert "symbol::utils.py::function::format_title" in refreshed.changed_symbols

    refreshed_context = ContextService(db_path).build_context(
        task="Use format_title in reporting",
        target="format_title",
        profile="small",
    )
    assert refreshed_context.target_node_id == "symbol::utils.py::function::format_title"
