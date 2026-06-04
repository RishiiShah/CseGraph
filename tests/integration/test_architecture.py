"""Integration tests for community summaries and architecture overview."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from csegraph._core.core.models import to_dict
from csegraph._core.graph.architecture import ArchitectureService
from csegraph._core.graph.communities import detect_communities
from csegraph._core.index.services import IndexService
from csegraph._core.postprocess import PostprocessService


def _index_and_postprocess(tmp_path: Path, files: dict[str, str]) -> str:
    repo = tmp_path / "repo"
    repo.mkdir()
    for name, content in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    db = str(tmp_path / "index.db")
    IndexService(db).index(str(repo), profile="small")
    PostprocessService(db).postprocess(level="full")
    return db


_MULTI_MODULE_FILES = {
    "auth/login.py": (
        "from auth.tokens import create_token\n"
        "\ndef login(user, pwd):\n    return create_token(user)\n"
    ),
    "auth/tokens.py": (
        "import hashlib\n"
        "\ndef create_token(user):\n    return hashlib.sha256(user.encode()).hexdigest()\n"
        "\ndef revoke_token(token):\n    pass\n"
    ),
    "api/views.py": (
        "from auth.login import login\n"
        "\ndef handle_login(request):\n    return login(request.user, request.pwd)\n"
        "\ndef handle_logout(request):\n    pass\n"
    ),
    "api/routes.py": (
        "from api.views import handle_login, handle_logout\n"
        "\ndef setup_routes(app):\n    app.route('/login', handle_login)\n"
        "    app.route('/logout', handle_logout)\n"
    ),
    "tests/test_auth.py": (
        "from auth.login import login\n"
        "\ndef test_login():\n    assert login('a', 'b')\n"
    ),
}


class TestArchitectureOverview:
    def test_returns_result_with_summaries(self, tmp_path):
        db = _index_and_postprocess(tmp_path, _MULTI_MODULE_FILES)
        result = ArchitectureService(db).overview()
        assert result.command == "architecture"
        assert result.num_communities >= 1
        assert len(result.summaries) >= 1

    def test_summaries_have_labels(self, tmp_path):
        db = _index_and_postprocess(tmp_path, _MULTI_MODULE_FILES)
        result = ArchitectureService(db).overview()
        for s in result.summaries:
            assert s.label
            assert s.size > 0

    def test_summaries_include_key_symbols(self, tmp_path):
        db = _index_and_postprocess(tmp_path, _MULTI_MODULE_FILES)
        result = ArchitectureService(db).overview()
        all_key = [ks for s in result.summaries for ks in s.key_symbols]
        assert len(all_key) >= 1
        for ks in all_key:
            assert "name" in ks
            assert "kind" in ks
            assert "degree" in ks

    def test_summaries_include_language_breakdown(self, tmp_path):
        db = _index_and_postprocess(tmp_path, _MULTI_MODULE_FILES)
        result = ArchitectureService(db).overview()
        has_lang = any(s.languages for s in result.summaries)
        assert has_lang

    def test_summaries_have_edge_counts(self, tmp_path):
        db = _index_and_postprocess(tmp_path, _MULTI_MODULE_FILES)
        result = ArchitectureService(db).overview()
        total_internal = sum(s.internal_edges for s in result.summaries)
        assert total_internal >= 1

    def test_coupling_pairs_detected(self, tmp_path):
        db = _index_and_postprocess(tmp_path, _MULTI_MODULE_FILES)
        result = ArchitectureService(db).overview()
        if result.num_communities > 1:
            assert len(result.coupling) >= 1
            for cp in result.coupling:
                assert cp.weight >= 1
                assert cp.relations

    def test_empty_repo(self, tmp_path):
        repo = tmp_path / "empty"
        repo.mkdir()
        db = str(tmp_path / "index.db")
        IndexService(db).index(str(repo), profile="small")
        result = ArchitectureService(db).overview()
        assert result.num_communities == 0
        assert result.summaries == []
        assert "No communities" in result.warnings[0]

    def test_single_file_repo(self, tmp_path):
        db = _index_and_postprocess(tmp_path, {
            "main.py": "def hello():\n    pass\n",
        })
        result = ArchitectureService(db).overview()
        assert result.num_communities >= 1
        assert result.summaries[0].size >= 1

    def test_limit_parameter(self, tmp_path):
        db = _index_and_postprocess(tmp_path, _MULTI_MODULE_FILES)
        result = ArchitectureService(db).overview(limit=1)
        assert len(result.summaries) <= 1

    def test_serializable(self, tmp_path):
        db = _index_and_postprocess(tmp_path, _MULTI_MODULE_FILES)
        result = ArchitectureService(db).overview()
        payload = to_dict(result)
        serialized = json.dumps(payload)
        assert isinstance(json.loads(serialized), dict)

    def test_summaries_sorted_by_size(self, tmp_path):
        db = _index_and_postprocess(tmp_path, _MULTI_MODULE_FILES)
        result = ArchitectureService(db).overview()
        sizes = [s.size for s in result.summaries]
        assert sizes == sorted(sizes, reverse=True)

    def test_coupling_sorted_by_weight(self, tmp_path):
        db = _index_and_postprocess(tmp_path, _MULTI_MODULE_FILES)
        result = ArchitectureService(db).overview()
        weights = [cp.weight for cp in result.coupling]
        assert weights == sorted(weights, reverse=True)

    def test_no_communities_without_postprocess(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "a.py").write_text("def f(): pass\n", encoding="utf-8")
        db = str(tmp_path / "index.db")
        IndexService(db).index(str(repo), profile="small")
        result = ArchitectureService(db).overview()
        assert result.num_communities == 0
        assert any("No communities" in w for w in result.warnings)

    def test_type_counts_present(self, tmp_path):
        db = _index_and_postprocess(tmp_path, _MULTI_MODULE_FILES)
        result = ArchitectureService(db).overview()
        has_types = any(s.type_counts for s in result.summaries)
        assert has_types

    def test_test_count_tracked(self, tmp_path):
        db = _index_and_postprocess(tmp_path, _MULTI_MODULE_FILES)
        result = ArchitectureService(db).overview()
        total_tests = sum(s.test_count for s in result.summaries)
        assert total_tests >= 1


class TestArchitectureMCP:
    def test_tool_is_cli_only(self):
        from csegraph._core.server.app import _handle_tool

        with pytest.raises(ValueError, match="Unknown tool"):
            _handle_tool("csegraph_architecture", {})

    def test_prompt_is_not_agent_facing(self):
        from csegraph._core.server.app import _handle_prompt

        with pytest.raises(ValueError, match="Unknown prompt"):
            _handle_prompt("csegraph-architecture", {"repo": "/repo"})
