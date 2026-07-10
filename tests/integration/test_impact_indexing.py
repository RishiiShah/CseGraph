from __future__ import annotations

import json
import sqlite3

import pytest

from csegraph._core.index import services as index_services
from csegraph._core.index.repository import ProjectIndex
from csegraph._core.index.services import IndexService, RefreshService


def _db_path(repo):
    return repo / ".scratch" / "csegraph" / "impact.db"


def _write_resolution_repo(repo):
    repo.mkdir(parents=True)
    (repo / "pricing_a.py").write_text(
        "def calculate(value):\n    return value + 1\n",
        encoding="utf-8",
    )
    (repo / "pricing_b.py").write_text(
        "def calculate(value):\n    return value * 2\n",
        encoding="utf-8",
    )
    (repo / "checkout.py").write_text(
        "import requests\n"
        "from pricing_b import calculate as compute\n\n"
        "def checkout(subtotal):\n"
        "    return compute(subtotal)\n",
        encoding="utf-8",
    )
    (repo / "loose.py").write_text(
        "def run(value):\n    return calculate(value)\n",
        encoding="utf-8",
    )
    (repo / "local.py").write_text(
        "def calculate(value):\n    return value\n\ndef run(value):\n    return calculate(value)\n",
        encoding="utf-8",
    )
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_checkout.py").write_text(
        "from checkout import checkout\n\ndef test_checkout():\n    assert checkout(10) == 20\n",
        encoding="utf-8",
    )


def test_import_bindings_occurrences_and_ambiguity(tmp_path):
    repo = tmp_path / "repo"
    db_path = _db_path(repo)
    _write_resolution_repo(repo)

    IndexService(db_path).index(repo)

    with sqlite3.connect(db_path) as conn:
        binding = conn.execute(
            """
            SELECT local_name, imported_name, resolved_file_id,
                   resolved_symbol_id, resolution_status
            FROM import_bindings
            WHERE file_id = 'file::checkout.py' AND local_name = 'compute'
            """
        ).fetchone()
        external = conn.execute(
            """
            SELECT resolution_status
            FROM import_bindings
            WHERE file_id = 'file::checkout.py' AND local_name = 'requests'
            """
        ).fetchone()
        aliased_call = conn.execute(
            """
            SELECT target, start_line, source_text, resolution_status, resolution_strategy
            FROM edge_occurrences
            WHERE enclosing_symbol_id = 'symbol::checkout.py::function::checkout'
              AND relation = 'calls' AND name = 'compute'
            """
        ).fetchone()
        ambiguous = conn.execute(
            """
            SELECT target, resolution_status, candidate_targets
            FROM edge_occurrences
            WHERE enclosing_symbol_id = 'symbol::loose.py::function::run'
              AND relation = 'calls' AND name = 'calculate'
            """
        ).fetchone()
        local = conn.execute(
            """
            SELECT target, resolution_status, resolution_strategy
            FROM edge_occurrences
            WHERE enclosing_symbol_id = 'symbol::local.py::function::run'
              AND relation = 'calls' AND name = 'calculate'
            """
        ).fetchone()
        false_edges = conn.execute(
            """
            SELECT COUNT(*)
            FROM edges
            WHERE source = 'symbol::loose.py::function::run' AND relation = 'calls'
            """
        ).fetchone()[0]

    pricing_b = "symbol::pricing_b.py::function::calculate"
    assert binding == ("compute", "calculate", "file::pricing_b.py", pricing_b, "resolved")
    assert external == ("external",)
    assert aliased_call == (pricing_b, 5, "compute(subtotal)", "resolved", "explicit_import")
    assert ambiguous[0:2] == (None, "ambiguous")
    assert set(json.loads(ambiguous[2])) == {
        "symbol::local.py::function::calculate",
        "symbol::pricing_a.py::function::calculate",
        pricing_b,
    }
    assert local == (
        "symbol::local.py::function::calculate",
        "resolved",
        "same_file",
    )
    assert false_edges == 0


def test_index_and_refresh_keep_lexical_storage_in_sync(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "app.py"
    source.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    db_path = _db_path(repo)

    IndexService(db_path).index(repo)

    with sqlite3.connect(db_path) as conn:
        indexed_document = conn.execute(
            """
            SELECT node_id, name FROM lexical_documents
            WHERE node_id = 'symbol::app.py::function::alpha'
            """
        ).fetchone()
        indexed_match = conn.execute(
            """
            SELECT node_id FROM lexical_index
            WHERE lexical_index MATCH 'alpha'
            """
        ).fetchall()

    source.write_text("def beta():\n    return 2\n", encoding="utf-8")
    RefreshService(db_path).refresh(changed_paths=[source])

    with sqlite3.connect(db_path) as conn:
        stale_match = conn.execute(
            "SELECT node_id FROM lexical_index WHERE lexical_index MATCH 'alpha'"
        ).fetchall()
        refreshed_document = conn.execute(
            """
            SELECT node_id, name FROM lexical_documents
            WHERE node_id = 'symbol::app.py::function::beta'
            """
        ).fetchone()
        refreshed_match = conn.execute(
            "SELECT node_id FROM lexical_index WHERE lexical_index MATCH 'beta'"
        ).fetchall()

    source.unlink()
    RefreshService(db_path).refresh(changed_paths=[source])

    with sqlite3.connect(db_path) as conn:
        remaining_documents = conn.execute("SELECT COUNT(*) FROM lexical_documents").fetchone()[0]
        deleted_match = conn.execute(
            "SELECT node_id FROM lexical_index WHERE lexical_index MATCH 'beta'"
        ).fetchall()

    assert indexed_document == ("symbol::app.py::function::alpha", "alpha")
    assert ("symbol::app.py::function::alpha",) in indexed_match
    assert stale_match == []
    assert refreshed_document == ("symbol::app.py::function::beta", "beta")
    assert ("symbol::app.py::function::beta",) in refreshed_match
    assert remaining_documents == 0
    assert deleted_match == []


def test_index_and_refresh_keep_module_and_symbol_lookups_in_sync(tmp_path):
    repo = tmp_path / "repo"
    package = repo / "pkg"
    package.mkdir(parents=True)
    source = package / "greeter.py"
    source.write_text(
        "class Greeter:\n    def greet(self):\n        return 'hello'\n",
        encoding="utf-8",
    )
    db_path = _db_path(repo)

    IndexService(db_path).index(repo)

    with sqlite3.connect(db_path) as conn:
        module_row = conn.execute("SELECT module_name, file_id FROM module_lookup").fetchone()
        initial_aliases = conn.execute(
            """
            SELECT lookup_name
            FROM symbol_lookup AS lookup
            JOIN symbols AS symbol ON symbol.id = lookup.symbol_id
            WHERE symbol.name = 'Greeter.greet'
            ORDER BY lookup_name
            """
        ).fetchall()

    source.write_text(
        "class Greeter:\n    def welcome(self):\n        return 'hello'\n",
        encoding="utf-8",
    )
    RefreshService(db_path).refresh(changed_paths=[source], dependents_limit=0)

    with sqlite3.connect(db_path) as conn:
        stale_aliases = conn.execute(
            """
            SELECT lookup_name
            FROM symbol_lookup
            WHERE lookup_name IN ('Greeter.greet', 'greet')
            """
        ).fetchall()
        refreshed_aliases = conn.execute(
            """
            SELECT lookup_name
            FROM symbol_lookup AS lookup
            JOIN symbols AS symbol ON symbol.id = lookup.symbol_id
            WHERE symbol.name = 'Greeter.welcome'
            ORDER BY lookup_name
            """
        ).fetchall()

    assert module_row == ("pkg.greeter", "file::pkg/greeter.py")
    assert initial_aliases == [("Greeter.greet",), ("greet",)]
    assert stale_aliases == []
    assert refreshed_aliases == [("Greeter.welcome",), ("welcome",)]


def test_refresh_preserves_duplicate_module_fallback(tmp_path):
    repo = tmp_path / "repo"
    package = repo / "foo"
    package.mkdir(parents=True)
    module = repo / "foo.py"
    module.write_text("value = 'module'\n", encoding="utf-8")
    package_init = package / "__init__.py"
    package_init.write_text("value = 'package'\n", encoding="utf-8")
    importer = repo / "consumer.py"
    importer.write_text("from foo import value\n", encoding="utf-8")
    db_path = _db_path(repo)

    IndexService(db_path).index(repo)

    with sqlite3.connect(db_path) as conn:
        initial_candidates = conn.execute(
            """
            SELECT file_id
            FROM module_lookup
            WHERE module_name = 'foo'
            ORDER BY file_id
            """
        ).fetchall()

    package_init.unlink()
    RefreshService(db_path).refresh(changed_paths=[package_init])

    with sqlite3.connect(db_path) as conn:
        remaining_candidates = conn.execute(
            """
            SELECT file_id
            FROM module_lookup
            WHERE module_name = 'foo'
            ORDER BY file_id
            """
        ).fetchall()
        resolved_import = conn.execute(
            """
            SELECT resolved_file_id
            FROM imports
            WHERE file_id = 'file::consumer.py' AND import_name = 'foo'
            """
        ).fetchone()

    assert initial_candidates == [
        ("file::foo.py",),
        ("file::foo/__init__.py",),
    ]
    assert remaining_candidates == [("file::foo.py",)]
    assert resolved_import == ("file::foo.py",)


def test_resolver_lookups_load_only_requested_keys(tmp_path):
    repo = tmp_path / "repo"
    package = repo / "pkg"
    package.mkdir(parents=True)
    (package / "greeter.py").write_text(
        "class Greeter:\n    def greet(self):\n        return 'hello'\n",
        encoding="utf-8",
    )
    db_path = _db_path(repo)
    IndexService(db_path).index(repo)
    statements: list[str] = []
    index = ProjectIndex(db_path)
    try:
        index.conn.set_trace_callback(statements.append)
        batch = index_services._WriteBatch()
        index_services._load_symbol_lookup(index, batch)
        module_lookup = index_services._module_to_file_id(index)

        qualified = batch.symbol_by_name.get("Greeter.greet", [])
        short = batch.symbol_by_name.get("greet", [])
        module_file = module_lookup.get("pkg.greeter")
    finally:
        index.close()

    expected_symbol = "symbol::pkg/greeter.py::method::Greeter.greet"
    assert qualified == [expected_symbol]
    assert short == [expected_symbol]
    assert module_file == "file::pkg/greeter.py"
    assert not any("FROM entities" in statement for statement in statements)
    assert not any(statement == "SELECT path FROM files" for statement in statements)
    assert sum("WHERE lookup.lookup_name =" in statement for statement in statements) == 2
    assert sum("WHERE module_name =" in statement for statement in statements) == 1


def test_lazy_symbol_lookup_implements_mapping_semantics(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def target():\n    return 1\n", encoding="utf-8")
    db_path = _db_path(repo)
    IndexService(db_path).index(repo)
    index = ProjectIndex(db_path)
    try:
        batch = index_services._WriteBatch()
        index_services._load_symbol_lookup(index, batch)

        assert "target" in batch.symbol_by_name
        assert "missing" not in batch.symbol_by_name
        assert batch.symbol_by_name.get("missing") is None
    finally:
        index.close()


def test_symbol_insertion_does_not_query_lookup_per_symbol(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "def first():\n    return 1\n\ndef second():\n    return first()\n",
        encoding="utf-8",
    )
    lookup_loads = 0
    loads_before_edge_insertion: list[int] = []
    real_load = index_services._LazySymbolLookup._load
    real_insert_edges = index_services._insert_edges

    def recording_load(lookup, name):
        nonlocal lookup_loads
        lookup_loads += 1
        return real_load(lookup, name)

    def recording_insert_edges(index, parsed_files, structural_edges, batch):
        loads_before_edge_insertion.append(lookup_loads)
        return real_insert_edges(index, parsed_files, structural_edges, batch)

    monkeypatch.setattr(index_services._LazySymbolLookup, "_load", recording_load)
    monkeypatch.setattr(index_services, "_insert_edges", recording_insert_edges)

    IndexService(_db_path(repo)).index(repo)

    assert loads_before_edge_insertion == [0]


def test_refresh_symbol_insertion_does_not_query_lookup_per_symbol(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "app.py"
    source.write_text("def original():\n    return 1\n", encoding="utf-8")
    (repo / "stable.py").write_text("def stable():\n    return 1\n", encoding="utf-8")
    db_path = _db_path(repo)
    IndexService(db_path).index(repo)
    source.write_text(
        "\n\n".join(f"def function_{index}():\n    return {index}" for index in range(100)) + "\n",
        encoding="utf-8",
    )
    lookup_loads = 0
    loads_before_edge_insertion: list[int] = []
    real_load = index_services._LazySymbolLookup._load
    real_insert_edges = index_services._insert_edges

    def recording_load(lookup, name):
        nonlocal lookup_loads
        lookup_loads += 1
        return real_load(lookup, name)

    def recording_insert_edges(index, parsed_files, structural_edges, batch):
        loads_before_edge_insertion.append(lookup_loads)
        return real_insert_edges(index, parsed_files, structural_edges, batch)

    monkeypatch.setattr(index_services._LazySymbolLookup, "_load", recording_load)
    monkeypatch.setattr(index_services, "_insert_edges", recording_insert_edges)

    RefreshService(db_path).refresh(changed_paths=[source], dependents_limit=0)

    assert loads_before_edge_insertion == [0]
    assert lookup_loads == 0


def test_candidate_refresh_reads_only_candidate_file_rows(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "target.py"
    target.write_text("def target():\n    return 1\n", encoding="utf-8")
    (repo / "stable_a.py").write_text("def stable_a():\n    return 1\n", encoding="utf-8")
    (repo / "stable_b.py").write_text("def stable_b():\n    return 1\n", encoding="utf-8")
    db_path = _db_path(repo)
    IndexService(db_path).index(repo)
    statements: list[str] = []
    real_init = ProjectIndex.__init__

    def recording_init(index, path):
        real_init(index, path)
        index.conn.set_trace_callback(statements.append)

    monkeypatch.setattr(ProjectIndex, "__init__", recording_init)
    monkeypatch.setattr(
        index_services,
        "git_untracked_paths",
        lambda _repo: pytest.fail("candidate refresh performed untracked discovery"),
    )

    result = RefreshService(db_path).refresh(changed_paths=[target], dependents_limit=0)

    normalized = [" ".join(statement.split()) for statement in statements]
    assert result.unchanged_files == ["target.py"]
    assert "SELECT path, sha256 FROM files" not in normalized
    assert any(
        statement.startswith("SELECT path, sha256 FROM files WHERE path IN")
        for statement in normalized
    )


def test_cold_index_uses_bulk_lexical_rebuild(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def target():\n    return 1\n", encoding="utf-8")
    calls: list[str] = []
    real_begin = ProjectIndex.begin_bulk_lexical_write
    real_finish = ProjectIndex.finish_bulk_lexical_write

    def recording_begin(index: ProjectIndex) -> None:
        calls.append("begin")
        real_begin(index)

    def recording_finish(index: ProjectIndex) -> None:
        calls.append("finish")
        real_finish(index)

    monkeypatch.setattr(ProjectIndex, "begin_bulk_lexical_write", recording_begin)
    monkeypatch.setattr(ProjectIndex, "finish_bulk_lexical_write", recording_finish)

    IndexService(_db_path(repo)).index(repo)

    assert calls == ["begin", "finish"]


def test_cold_index_defers_secondary_indexes(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def target():\n    return 1\n", encoding="utf-8")
    calls: list[str] = []
    real_begin = ProjectIndex.begin_bulk_secondary_index_write
    real_finish = ProjectIndex.finish_bulk_secondary_index_write

    def recording_begin(index: ProjectIndex) -> None:
        calls.append("begin")
        real_begin(index)

    def recording_finish(index: ProjectIndex) -> None:
        calls.append("finish")
        real_finish(index)

    monkeypatch.setattr(ProjectIndex, "begin_bulk_secondary_index_write", recording_begin)
    monkeypatch.setattr(ProjectIndex, "finish_bulk_secondary_index_write", recording_finish)

    IndexService(_db_path(repo)).index(repo)

    assert calls == ["begin", "finish"]


@pytest.mark.parametrize("change", ["rename", "delete"])
def test_refresh_replaces_changed_files_without_history(tmp_path, change):
    repo = tmp_path / "repo"
    repo.mkdir()
    child = repo / "child.py"
    child.write_text(
        "def helper(value):\n    return value + 1\n",
        encoding="utf-8",
    )
    (repo / "caller.py").write_text(
        "from child import helper\n\ndef parent(value):\n    return helper(value)\n",
        encoding="utf-8",
    )
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_parent.py").write_text(
        "from caller import parent\n\ndef test_parent():\n    assert parent(1) == 2\n",
        encoding="utf-8",
    )
    db_path = _db_path(repo)
    IndexService(db_path).index(repo)

    if change == "rename":
        child.write_text(
            "def new_helper(value):\n    return value + 1\n",
            encoding="utf-8",
        )
    else:
        child.unlink()

    result = RefreshService(db_path).refresh(
        changed_paths=[child],
    )

    old_helper = "symbol::child.py::function::helper"
    with sqlite3.connect(db_path) as conn:
        old_symbol = conn.execute("SELECT 1 FROM symbols WHERE id = ?", (old_helper,)).fetchone()
        old_occurrences = conn.execute(
            "SELECT 1 FROM edge_occurrences WHERE target = ?",
            (old_helper,),
        ).fetchall()
        current_call = conn.execute(
            """
            SELECT target, resolution_status
            FROM edge_occurrences
            WHERE enclosing_symbol_id = 'symbol::caller.py::function::parent'
              AND relation = 'calls' AND name = 'helper'
            """
        ).fetchone()
        foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()

    assert "caller.py" in result.changed_files or result.dependents_expanded >= 1
    assert old_symbol is None
    assert old_occurrences == []
    assert current_call == (None, "unresolved")
    assert foreign_keys == []


def test_refresh_scopes_orphan_cleanup_to_replaced_symbols(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    child = repo / "child.py"
    child.write_text("def helper(value):\n    return value + 1\n", encoding="utf-8")
    (repo / "caller.py").write_text(
        "from child import helper\n\ndef parent(value):\n    return helper(value)\n",
        encoding="utf-8",
    )
    db_path = _db_path(repo)
    IndexService(db_path).index(repo)

    def fail_global_cleanup(*args, **kwargs):
        raise AssertionError("refresh should not scan the full entities view")

    monkeypatch.setattr(ProjectIndex, "cleanup_orphan_edges", fail_global_cleanup)
    old_helper = "symbol::child.py::function::helper"
    parent = "symbol::caller.py::function::parent"
    unrelated_missing = "symbol::unrelated.py::function::missing"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO edges(source, target, relation, confidence, confidence_tier)
            VALUES(?, ?, 'calls', 1.0, 'EXTRACTED')
            """,
            (parent, unrelated_missing),
        )

    child.write_text("def helper(value):\n    return value + 2\n", encoding="utf-8")
    RefreshService(db_path).refresh(changed_paths=[child], dependents_limit=0)

    with sqlite3.connect(db_path) as conn:
        preserved = conn.execute(
            "SELECT 1 FROM edges WHERE source = ? AND target = ? AND relation = 'calls'",
            (parent, old_helper),
        ).fetchone()

    child.write_text("def new_helper(value):\n    return value + 2\n", encoding="utf-8")
    RefreshService(db_path).refresh(changed_paths=[child], dependents_limit=0)

    with sqlite3.connect(db_path) as conn:
        removed = conn.execute(
            "SELECT 1 FROM edges WHERE target = ?",
            (old_helper,),
        ).fetchone()
        unrelated_preserved = conn.execute(
            "SELECT 1 FROM edges WHERE target = ?",
            (unrelated_missing,),
        ).fetchone()

    assert preserved == (1,)
    assert removed is None
    assert unrelated_preserved == (1,)


def test_refresh_writes_changed_and_dependent_files_in_one_batch(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    child = repo / "child.py"
    child.write_text("def helper(value):\n    return value + 1\n", encoding="utf-8")
    (repo / "caller.py").write_text(
        "from child import helper\n\ndef parent(value):\n    return helper(value)\n",
        encoding="utf-8",
    )
    db_path = _db_path(repo)
    IndexService(db_path).index(repo)
    calls: list[list[str]] = []
    real_write = index_services._write_parsed_files

    def recording_write(index, repo_root, parsed_files):
        calls.append([parsed.rel_path for parsed in parsed_files])
        return real_write(index, repo_root, parsed_files)

    monkeypatch.setattr(index_services, "_write_parsed_files", recording_write)
    child.write_text("def helper(value):\n    return value + 2\n", encoding="utf-8")

    result = RefreshService(db_path).refresh(changed_paths=[child])

    assert result.dependents_expanded == 1
    assert len(calls) == 1
    assert set(calls[0]) == {"caller.py", "child.py"}
    assert "symbol::caller.py::function::parent" not in result.changed_symbols


def test_dependent_limit_excludes_processed_paths_before_sql_limit(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "target.py").write_text("def changed():\n    return 1\n", encoding="utf-8")
    for name in ("a.py", "b.py", "c.py", "d.py"):
        (repo / name).write_text("VALUE = 1\n", encoding="utf-8")
    db_path = _db_path(repo)
    IndexService(db_path).index(repo)
    target = "file::target.py"
    statements: list[str] = []
    index = ProjectIndex(db_path)
    try:
        index.conn.executemany(
            """
            INSERT INTO edges(source, target, relation, confidence, confidence_tier)
            VALUES(?, ?, 'imports', 1.0, 'EXTRACTED')
            """,
            [(f"file::{name}", target) for name in ("a.py", "b.py", "c.py", "d.py")],
        )
        index.conn.commit()
        index.conn.set_trace_callback(statements.append)

        paths, cap_hit = index_services._find_dependent_files(
            index,
            [target],
            {"a.py", "b.py"},
            2,
        )
        dependent_query = next(
            statement for statement in statements if "WITH changed(id)" in statement
        )
        query_plan = [
            str(row["detail"])
            for row in index.conn.execute(f"EXPLAIN QUERY PLAN {dependent_query}")
        ]
    finally:
        index.close()

    assert paths == ["c.py", "d.py"]
    assert cap_hit is False
    assert not any("JOIN entities" in statement for statement in statements)
    assert not any(detail.startswith(("SCAN f ", "SCAN s ")) for detail in query_plan)


def test_dependent_lookup_follows_decorator_edge_direction(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "decorator.py").write_text("def decorate(value):\n    return value\n", encoding="utf-8")
    (repo / "model.py").write_text("def model():\n    return 1\n", encoding="utf-8")
    db_path = _db_path(repo)
    IndexService(db_path).index(repo)
    decorator = "symbol::decorator.py::function::decorate"
    decorated = "symbol::model.py::function::model"
    index = ProjectIndex(db_path)
    try:
        index.conn.execute(
            """
            INSERT INTO edges(source, target, relation, confidence, confidence_tier)
            VALUES(?, ?, 'decorates', 1.0, 'EXTRACTED')
            """,
            (decorator, decorated),
        )
        index.conn.commit()

        decorator_dependents, _ = index_services._find_dependent_files(
            index,
            [decorator],
            set(),
            5,
        )
        decorated_dependents, _ = index_services._find_dependent_files(
            index,
            [decorated],
            set(),
            5,
        )
    finally:
        index.close()

    assert decorator_dependents == ["model.py"]
    assert decorated_dependents == []


def test_refresh_expands_file_import_dependents(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "target.py"
    target.write_text("def changed():\n    return 1\n", encoding="utf-8")
    (repo / "consumer.py").write_text(
        "from target import changed\n\nVALUE = changed\n",
        encoding="utf-8",
    )
    db_path = _db_path(repo)
    IndexService(db_path).index(repo)
    target.write_text("def changed():\n    return 2\n", encoding="utf-8")

    result = RefreshService(db_path).refresh(changed_paths=[target])

    assert result.changed_files == ["target.py"]
    assert result.dependents_expanded == 1
