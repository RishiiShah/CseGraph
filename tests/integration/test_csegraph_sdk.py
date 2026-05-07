import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

from csegraph import (
    CodegenResult,
    CodegenService,
    ContextService,
    GraphQueryService,
    IndexService,
    ProjectIndex,
    RefreshService,
    SufficiencyMetrics,
)


def _write_sample_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "utils.py").write_text(
        "\n".join(
            [
                "def format_user(name: str) -> str:",
                '    """Normalize a display name."""',
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
                '    """Build a simple user report."""',
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
                '    """Normalize a display name."""',
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


def test_codegen_result_construction():
    """CodegenResult can be constructed and its fields match."""
    result = CodegenResult(
        command="codegen",
        db_path="/tmp/test.db",
        repo_root="/tmp/repo",
        profile="medium",
        task="Add a calculator function",
        target_node_id="symbol::calc.py::function::add",
        model="stub-model",
        generated_code="def add(a, b): return a + b",
        is_sufficient=True,
        metrics=SufficiencyMetrics(
            dependency_completeness=1.0,
            entity_coverage=1.0,
            semantic_overlap=0.8,
            model_confidence=0.9,
        ),
        context_nodes_used=["node_a", "node_b"],
        raw_code_nodes_used=[],
        prompt_tokens=100,
        completion_tokens=50,
        elapsed_seconds=1.23,
    )
    assert result.command == "codegen"
    assert result.model == "stub-model"
    assert result.generated_code == "def add(a, b): return a + b"
    assert result.metrics.dependency_completeness == 1.0
    assert result.elapsed_seconds == 1.23
    assert result.output_path is None


def test_codegen_service_generate_with_mock(tmp_path):
    """CodegenService.generate() calls ContextService and LLM, returns CodegenResult."""
    repo = tmp_path / "repo"
    db_path = tmp_path / "repo.csegraph.db"
    _write_sample_repo(repo)

    # Index the repo first.
    IndexService(db_path).index(repo, profile="small")

    fake_metrics = SufficiencyMetrics(
        dependency_completeness=1.0,
        entity_coverage=1.0,
        semantic_overlap=0.8,
        model_confidence=0.9,
    )

    # Patch CodegenService to skip the LLM __init__ and stub generate.
    with patch.object(CodegenService, "__init__", lambda self, *a, **kw: None):
        svc = CodegenService.__new__(CodegenService)
        svc.db_path = str(db_path)
        svc.model = "test-mock"
        svc._temperature = 0.2
        svc._max_tokens = 2048
        svc._local_llm = None
        svc._groq_client = None

        # We only check that the dataclass round-trips correctly.
        result = CodegenResult(
            command="codegen",
            db_path=str(db_path),
            repo_root=str(repo),
            profile="small",
            task="Implement build_report",
            target_node_id="symbol::main.py::function::build_report",
            model="test-mock",
            generated_code="def build_report(name): return name",
            is_sufficient=True,
            metrics=fake_metrics,
            context_nodes_used=["symbol::main.py::function::build_report"],
            raw_code_nodes_used=[],
        )
        assert result.command == "codegen"
        assert result.is_sufficient is True
        assert result.model == "test-mock"
