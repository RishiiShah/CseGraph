import json
import sqlite3
from pathlib import Path

import pytest
from csegraph import (
    ContextService,
    GraphQueryService,
    IndexService,
    ProjectIndex,
    RefreshService,
)
from csegraph_core.core.errors import UnsupportedSchemaError
from csegraph_core.retrieval.constants import VALID_REASONS


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
    assert "nodes" in tables
    assert "edges" in tables
    assert "summaries" in tables
    assert "lexical_index" in tables
    assert "embedding_cache" in tables
    assert "retrieval_runs" in tables
    assert "retrieval_context" in tables
    assert "files" not in tables
    assert "symbols" not in tables

    with sqlite3.connect(db_path) as conn:
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version[0] == "csegraph-sqlite-v4"
    assert user_version == 4


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

    context_ids = {node.node_id for node in context.nodes}
    assert context.target == "symbol::main.py::function::build_report"
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
    assert refreshed_context.target == "symbol::utils.py::function::format_title"


def test_context_auto_includes_source_for_target_and_direct_dependencies(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "repo.csegraph.db"
    _write_sample_repo(repo)
    IndexService(db_path).index(repo, profile="small")

    context = ContextService(db_path).build_context(
        task="Implement build_report using format_user",
        target="build_report",
        profile="small",
    )

    by_id = {node.node_id: node for node in context.nodes}
    target = by_id["symbol::main.py::function::build_report"]
    helper = by_id["symbol::utils.py::function::format_user"]

    assert "def build_report(name: str) -> str:" in target.source_text
    assert "return f'Report: {format_user(name)}'" in target.source_text
    assert "def format_user(name: str) -> str:" in helper.source_text
    assert "return name.strip().title()" in helper.source_text
    assert target.estimated_tokens >= 1
    assert helper.estimated_tokens >= 1
    assert context.estimated_tokens >= target.estimated_tokens + helper.estimated_tokens
    assert "target" in target.reason
    assert "direct_call" in helper.reason
    assert set(target.reason).issubset(VALID_REASONS)
    assert set(helper.reason).issubset(VALID_REASONS)
    assert target.explanation is None
    assert helper.explanation is None


def test_context_explain_populates_human_explanations(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "repo.csegraph.db"
    _write_sample_repo(repo)
    IndexService(db_path).index(repo, profile="small")

    context = ContextService(db_path).build_context(
        task="Implement build_report using format_user",
        target="build_report",
        profile="small",
        explain=True,
    )

    by_id = {node.node_id: node for node in context.nodes}
    assert by_id["symbol::main.py::function::build_report"].explanation
    assert "target" in by_id["symbol::main.py::function::build_report"].reason
    helper = by_id["symbol::utils.py::function::format_user"]
    assert "direct_call" in helper.reason
    assert "directly called by the target" in helper.explanation


def test_context_include_source_never_stays_compact(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "repo.csegraph.db"
    _write_sample_repo(repo)
    IndexService(db_path).index(repo, profile="small")

    context = ContextService(db_path).build_context(
        task="Implement build_report using format_user",
        target="build_report",
        profile="small",
        include_source="never",
    )

    assert context.nodes
    assert all(node.source_text is None for node in context.nodes)
    assert all(node.estimated_tokens >= 1 for node in context.nodes)
    assert context.estimated_tokens == sum(node.estimated_tokens for node in context.nodes)


def test_context_max_tokens_limits_source_materialization(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "repo.csegraph.db"
    _write_sample_repo(repo)
    IndexService(db_path).index(repo, profile="small")

    context = ContextService(db_path).build_context(
        task="Implement build_report using format_user",
        target="build_report",
        profile="small",
        include_source="always",
        max_tokens=30,
    )

    by_id = {node.node_id: node for node in context.nodes}
    assert context.estimated_tokens <= 30
    assert "symbol::main.py::function::build_report" in by_id
    helper = by_id.get("symbol::utils.py::function::format_user")
    assert helper is None or helper.source_text is None


def test_context_reason_enum_is_strict(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "repo.csegraph.db"
    _write_sample_repo(repo)
    IndexService(db_path).index(repo, profile="small")

    context = ContextService(db_path).build_context(
        task="Implement build_report using format_user",
        target="build_report",
        profile="small",
    )

    for node in context.nodes:
        assert node.reason
        assert set(node.reason).issubset(VALID_REASONS)
        assert all("expanded-from-" not in reason for reason in node.reason)


def test_context_service_config_path_overrides_thresholds(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "repo.csegraph.db"
    _write_sample_repo(repo)
    IndexService(db_path).index(repo, profile="small")

    config_file = tmp_path / "csegraph.json"
    config_file.write_text(
        json.dumps({"dep_threshold": 0.65}),
        encoding="utf-8",
    )

    context = ContextService(db_path).build_context(
        task="Implement build_report using format_user",
        target="symbol::main.py::function::build_report",
        config_path=str(config_file),
    )
    assert context.thresholds["dependency_completeness"] == 0.65
    assert "semantic_overlap_relaxed" in context.thresholds


def test_v12_emits_inherits_decorates_and_tested_by(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "base.py").write_text(
        "class Animal:\n    def speak(self): return ''\n\ndef cached(fn):\n    return fn\n",
        encoding="utf-8",
    )
    (repo / "dog.py").write_text(
        "from base import Animal, cached\n\nclass Dog(Animal):\n    @cached\n    def speak(self): return 'woof'\n",
        encoding="utf-8",
    )
    (repo / "tests").mkdir()
    (repo / "tests" / "test_dog.py").write_text(
        "from dog import Dog\n\ndef test_dog_speak():\n    assert Dog().speak() == 'woof'\n",
        encoding="utf-8",
    )

    db_path = tmp_path / "repo.db"
    IndexService(db_path).index(repo, profile="small")
    with sqlite3.connect(db_path) as conn:
        relations = {row[0] for row in conn.execute("SELECT DISTINCT relation FROM edges")}
        types = {row[0] for row in conn.execute("SELECT DISTINCT type FROM nodes")}
    assert {"inherits", "decorates", "tested_by"}.issubset(relations)
    assert {"repo", "folder", "file", "class", "function", "method"}.issubset(types)


def test_unsupported_schema_version_raises_structured_error(tmp_path):
    db_path = tmp_path / "future.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta(key, value) VALUES('schema_version', 'csegraph-sqlite-v999');
            """
        )

    idx = ProjectIndex(db_path)
    try:
        with pytest.raises(UnsupportedSchemaError) as exc_info:
            idx.initialize_schema()
    finally:
        idx.close()

    assert exc_info.value.error_code == "unsupported_schema"
    assert exc_info.value.hint == "Rebuild the index or install a compatible csegraph-core version."


def test_malformed_schema_metadata_raises_structured_error(tmp_path):
    db_path = tmp_path / "malformed.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE nodes (id TEXT PRIMARY KEY);
            """
        )

    idx = ProjectIndex(db_path)
    try:
        with pytest.raises(UnsupportedSchemaError) as exc_info:
            idx.initialize_schema()
    finally:
        idx.close()

    assert exc_info.value.error_code == "unsupported_schema"
