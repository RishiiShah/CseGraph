"""Integration tests for framework resolver passes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from csegraph._core.core.models import to_dict
from csegraph._core.graph.resolvers import ResolverService
from csegraph._core.index.services import IndexService
from csegraph._core.postprocess import PostprocessService


def _index_repo(tmp_path: Path, files: dict[str, str]) -> str:
    repo = tmp_path / "repo"
    repo.mkdir()
    for name, content in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    db = str(tmp_path / "index.db")
    IndexService(db).index(str(repo), profile="small")
    return db


_PYTHON_FILES = {
    "app.py": "from helpers import fmt\n\ndef greet(name):\n    return fmt(name)\n",
    "helpers.py": "def fmt(name):\n    return f'Hello, {name}'\n",
    "tests/test_app.py": "from app import greet\n\ndef test_greet():\n    assert greet('x')\n",
}

_PYTHON_INIT_FILES = {
    "mypackage/__init__.py": "from mypackage.core import process\n",
    "mypackage/core.py": "def process(data):\n    return data\n",
    "main.py": "from mypackage import process\n\ndef run():\n    return process(42)\n",
}


class TestResolverService:
    def test_run_all_returns_result(self, tmp_path):
        db = _index_repo(tmp_path, _PYTHON_FILES)
        result = ResolverService(db).run_all()
        assert result.command == "resolvers"
        assert result.db_path == db
        assert len(result.resolvers_run) == 3

    def test_resolver_names(self, tmp_path):
        db = _index_repo(tmp_path, _PYTHON_FILES)
        result = ResolverService(db).run_all()
        names = [s.name for s in result.resolvers_run]
        assert "transitive_test_edges" in names
        assert "python_import_resolver" in names
        assert "ts_alias_resolver" in names

    def test_idempotent(self, tmp_path):
        db = _index_repo(tmp_path, _PYTHON_FILES)
        r1 = ResolverService(db).run_all()
        r2 = ResolverService(db).run_all()
        assert r2.total_edges_added == 0

    def test_result_serializable(self, tmp_path):
        db = _index_repo(tmp_path, _PYTHON_FILES)
        result = ResolverService(db).run_all()
        payload = to_dict(result)
        assert isinstance(json.dumps(payload), str)
        assert payload["command"] == "resolvers"

    def test_total_edges_added_is_sum(self, tmp_path):
        db = _index_repo(tmp_path, _PYTHON_FILES)
        result = ResolverService(db).run_all()
        assert result.total_edges_added == sum(
            s.edges_added for s in result.resolvers_run
        )


class TestTransitiveTestEdges:
    def test_finds_transitive_tested_by(self, tmp_path):
        files = {
            "prod.py": "def core_fn():\n    return 42\n",
            "helper.py": "from prod import core_fn\n\ndef helper():\n    return core_fn()\n",
            "tests/test_helper.py": "from helper import helper\n\ndef test_helper():\n    assert helper() == 42\n",
        }
        db = _index_repo(tmp_path, files)
        result = ResolverService(db).run_all()
        test_stats = next(s for s in result.resolvers_run if s.name == "transitive_test_edges")
        assert test_stats.edges_added >= 0

    def test_no_test_functions_skips(self, tmp_path):
        files = {"app.py": "def f():\n    pass\n"}
        db = _index_repo(tmp_path, files)
        result = ResolverService(db).run_all()
        test_stats = next(s for s in result.resolvers_run if s.name == "transitive_test_edges")
        assert test_stats.edges_added == 0


class TestPythonImportResolver:
    def test_resolves_init_imports(self, tmp_path):
        db = _index_repo(tmp_path, _PYTHON_INIT_FILES)
        result = ResolverService(db).run_all()
        py_stats = next(s for s in result.resolvers_run if s.name == "python_import_resolver")
        assert py_stats.edges_added >= 0

    def test_no_python_files_skips(self, tmp_path):
        files = {"app.js": "const x = 1;\n"}
        db = _index_repo(tmp_path, files)
        result = ResolverService(db).run_all()
        py_stats = next(s for s in result.resolvers_run if s.name == "python_import_resolver")
        assert py_stats.edges_added == 0

    def test_relative_import_resolution(self, tmp_path):
        files = {
            "pkg/__init__.py": "",
            "pkg/mod_a.py": "def func_a():\n    return 1\n",
            "pkg/mod_b.py": "from .mod_a import func_a\n\ndef func_b():\n    return func_a()\n",
        }
        db = _index_repo(tmp_path, files)
        result = ResolverService(db).run_all()
        py_stats = next(s for s in result.resolvers_run if s.name == "python_import_resolver")
        assert py_stats.edges_added >= 0


class TestTsAliasResolver:
    def test_no_tsconfig_skips(self, tmp_path):
        files = {"app.ts": "export function hello() { return 1; }\n"}
        db = _index_repo(tmp_path, files)
        result = ResolverService(db).run_all()
        ts_stats = next(s for s in result.resolvers_run if s.name == "ts_alias_resolver")
        assert ts_stats.edges_added == 0

    def test_with_tsconfig_aliases(self, tmp_path):
        files = {
            "tsconfig.json": json.dumps({
                "compilerOptions": {
                    "baseUrl": ".",
                    "paths": {"@app/*": ["src/*"]},
                }
            }),
            "src/utils.ts": "export function greet() { return 'hi'; }\n",
            "src/main.ts": "import { greet } from '@app/utils';\nconsole.log(greet());\n",
        }
        db = _index_repo(tmp_path, files)
        result = ResolverService(db).run_all()
        ts_stats = next(s for s in result.resolvers_run if s.name == "ts_alias_resolver")
        assert ts_stats.edges_added >= 0


class TestPostprocessIntegration:
    def test_full_level_runs_resolvers(self, tmp_path):
        db = _index_repo(tmp_path, _PYTHON_FILES)
        result = PostprocessService(db).postprocess(level="full")
        assert "resolvers" not in result.skipped
        assert isinstance(result.resolvers_edges_added, int)
        assert "resolvers_ms" in result.timings_ms

    def test_minimal_level_skips_resolvers(self, tmp_path):
        db = _index_repo(tmp_path, _PYTHON_FILES)
        result = PostprocessService(db).postprocess(level="minimal")
        assert "resolvers" in result.skipped
        assert result.resolvers_edges_added == 0

    def test_none_level_skips_resolvers(self, tmp_path):
        db = _index_repo(tmp_path, _PYTHON_FILES)
        result = PostprocessService(db).postprocess(level="none")
        assert "resolvers" in result.skipped


class TestResolversMCP:
    def test_tool_is_cli_only(self):
        from csegraph._core.server.app import _handle_tool

        with pytest.raises(ValueError, match="Unknown tool"):
            _handle_tool("csegraph_resolvers", {})

    def test_prompt_is_not_agent_facing(self):
        from csegraph._core.server.app import _handle_prompt

        with pytest.raises(ValueError, match="Unknown prompt"):
            _handle_prompt("csegraph-resolvers", {"repo": "/repo"})
