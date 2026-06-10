"""Integration/regression tests verifying all correctness & performance fixes."""
from __future__ import annotations

import os
import pytest
from pathlib import Path

from csegraph._core.index.services import IndexService, RefreshService, _parse_one_cached, _pick_call_target
from csegraph._core.index.cache import ExtractionCache
from csegraph._core.index.repository import ProjectIndex
from csegraph._core.languages.registry import registry
from csegraph._core.retrieval.context import ContextService, _resolve_target, _build_detail_pass
from csegraph._core.graph.queries import _node_view_from_row, _path_step_from_row, _resolve_graph_node
from csegraph._core.retrieval.scoring import lexical_scores
from csegraph._core.text.entities import extract_query_entities
from csegraph._core.hooks import uninstall_hooks, install_hooks, HOOK_MARKER
from csegraph._core.languages.treesitter.languages import make_cpp_config

def test_symlink_dos_protection(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    cache = ExtractionCache(str(tmp_path / "cache.db"))
    parser = registry.for_extension(".py")

    # Path inside repo is fine
    inside_file = repo_root / "inside.py"
    inside_file.write_text("def foo(): pass", encoding="utf-8")
    parsed = _parse_one_cached(parser, inside_file, repo_root, cache)
    assert parsed.rel_path == "inside.py"

    # Path outside repo throws ValueError
    outside_file = tmp_path / "outside.py"
    outside_file.write_text("def bar(): pass", encoding="utf-8")
    with pytest.raises(ValueError, match="outside repository root"):
        _parse_one_cached(parser, outside_file, repo_root, cache)


def test_changed_paths_refresh_respects_csegraphignore(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    db = tmp_path / "index.db"
    (repo / ".csegraphignore").write_text("ignored.py\n", encoding="utf-8")
    (repo / "app.py").write_text("def app():\n    return 1\n", encoding="utf-8")

    IndexService(db).index(repo, profile="small")

    ignored = repo / "ignored.py"
    ignored.write_text("def hidden():\n    return 2\n", encoding="utf-8")
    refreshed = RefreshService(db).refresh(profile="small", changed_paths=[ignored])

    index = ProjectIndex(db)
    try:
        paths = {
            row["path"]
            for row in index.conn.execute("SELECT path FROM nodes WHERE type = 'file'")
        }
    finally:
        index.close()

    assert refreshed.changed_files == []
    assert "ignored.py" not in paths


def test_cross_file_method_linkage(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    db = str(tmp_path / "test.db")

    # Define a class in one file and a method receiver function in another
    (repo / "class_def.py").write_text("class MyClass:\n    pass\n", encoding="utf-8")
    index = ProjectIndex(db)
    index.initialize_schema()
    index.set_metadata(str(repo), "small")

    # Insert a class node
    index.conn.execute(
        "INSERT INTO nodes (id, parent_id, type, name, path, language, source_hash, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("symbol::class_def.py::class::MyClass", "file::class_def.py", "class", "MyClass", "class_def.py", "python", "hash1", 12345.6)
    )
    # Insert file node
    index.conn.execute(
        "INSERT INTO nodes (id, parent_id, type, name, path, language, source_hash, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("file::method_def.py", "repo::test", "file", "method_def.py", "method_def.py", "python", "hash0", 12345.6)
    )
    # Insert a method node in another file with receiver "MyClass"
    # But parent_id is file, so it needs resolving.
    metadata_json = '{"receiver": "MyClass"}'
    index.conn.execute(
        "INSERT INTO nodes (id, parent_id, type, name, path, metadata, language, source_hash, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("symbol::method_def.py::method::my_method", "file::method_def.py", "method", "MyClass.my_method", "method_def.py", metadata_json, "python", "hash2", 12345.6)
    )
    index.conn.commit()

    # Run resolution
    from csegraph._core.index.services import _resolve_cross_file_methods
    _resolve_cross_file_methods(index)

    # Check parent_id updated to the class node
    row = index.conn.execute("SELECT parent_id FROM nodes WHERE type = 'method'").fetchone()
    assert row["parent_id"] == "symbol::class_def.py::class::MyClass"


def test_lexical_heuristics_prefer_function():
    symbol_by_name = {
        "greet": ["opaque-method-id", "opaque-function-id"]
    }
    node_to_file_node = {
        "opaque-method-id": "file::app.py",
        "opaque-function-id": "file::helpers.py"
    }
    node_kind_by_id = {
        "opaque-method-id": "method",
        "opaque-function-id": "function",
    }
    # Non-local call picking function kind over method
    target = _pick_call_target(
        "greet",
        "file::other.py",
        symbol_by_name,
        node_to_file_node,
        node_kind_by_id,
    )
    assert target == "opaque-function-id"


def test_graph_fallback_views_do_not_parse_opaque_ids():
    opaque_id = "opaque::node::id"

    path_step = _path_step_from_row(opaque_id, {})
    node_view = _node_view_from_row(opaque_id, {})

    assert path_step.name == opaque_id
    assert node_view.name == opaque_id


def test_absolute_path_metadata_fallback(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    db = str(tmp_path / "test.db")
    index = ProjectIndex(db)
    index.initialize_schema()
    index.set_metadata(str(repo), "small")

    # Insert file node
    index.conn.execute(
        "INSERT INTO nodes (id, parent_id, type, name, path, language, source_hash, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("file::app.py", "repo::test", "file", "app.py", "app.py", "python", "hash3", 12345.6)
    )
    index.conn.commit()

    # Resolve target using empty repo_root fallback to metadata
    target_abs = str((repo / "app.py").resolve())
    resolved = _resolve_target(target_abs, "task", {}, {}, index, repo_root="")
    assert resolved == "file::app.py"

    # Resolve graph node with empty repo_root
    resolved_node = _resolve_graph_node(index, target_abs, repo_root="")
    assert resolved_node == "file::app.py"


def test_lexical_scoring_candidates_optimization():
    symbols = {
        "node1": {"name": "greet", "file_path": "app.py", "signature": "", "docstring": "", "language": "python"},
        "node2": {"name": "unrelated", "file_path": "helpers.py", "signature": "", "docstring": "", "language": "python"},
    }
    summaries = {}

    # We query for "greet"
    scores, evidence = lexical_scores("greet", symbols, summaries)
    assert "node1" in scores
    # Since node2 has no match in task lower, it is skipped for tokenization overlap check
    assert "fts5-bm25" not in evidence.get("node2", [])


def test_lexical_scoring_demotes_tests_for_production_queries():
    symbols = {
        "prod": {
            "name": "process_order",
            "file_path": "app.py",
            "signature": "def process_order()",
            "docstring": "",
            "language": "python",
        },
        "test": {
            "name": "test_process_order",
            "file_path": "tests/test_app.py",
            "signature": "def test_process_order()",
            "docstring": "",
            "language": "python",
        },
    }

    production_scores, _ = lexical_scores("process order implementation", symbols, {})
    test_scores, _ = lexical_scores("fix failing test process order", symbols, {})

    assert production_scores["prod"] > production_scores["test"]
    assert test_scores["test"] >= test_scores["prod"]


def test_entity_extraction_length_optimization():
    known_names = ["very_long_symbol_name_that_is_longer_than_query", "greet"]
    entities = extract_query_entities("greet name", known_names)
    assert "greet" in entities
    assert "very_long_symbol_name_that_is_longer_than_query" not in entities


def test_git_hook_uninstall_block_exact_replacement_safety(tmp_path):
    hook_dir = tmp_path / ".git" / "hooks"
    hook_dir.mkdir(parents=True)
    hook_file = hook_dir / "post-commit"

    user_script = "#!/bin/sh\n# user comment\nif true; then\n  echo 1\nfi\n"
    hook_file.write_text(user_script, encoding="utf-8")

    # Install csegraph hook
    install_hooks(tmp_path)
    content_installed = hook_file.read_text(encoding="utf-8")
    assert HOOK_MARKER in content_installed

    # Uninstall
    uninstall_hooks(tmp_path)
    content_uninstalled = hook_file.read_text(encoding="utf-8")
    assert HOOK_MARKER not in content_uninstalled
    # Verify user comment and user logic are completely preserved
    assert "# user comment" in content_uninstalled
    assert "echo 1" in content_uninstalled


def test_cpp_language_spec_file_suffixes():
    config = make_cpp_config()
    assert "_test" in config.test_file_suffixes
    assert "Test" in config.test_file_suffixes
    assert "Tests" in config.test_file_suffixes
