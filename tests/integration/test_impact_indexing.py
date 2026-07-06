from __future__ import annotations

import json
import sqlite3

import pytest

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
