"""Integration tests for P6-4: optional local-first embeddings."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import List

import pytest

from csegraph._core.core.models import to_dict
from csegraph._core.graph.embeddings import (
    EMBEDDING_PROVIDERS,
    EmbeddingService,
    _blob_to_vector,
    _cosine_similarity,
    _rrf_merge,
    _vector_to_blob,
)
from csegraph._core.index.services import IndexService
from csegraph._core.postprocess import PostprocessService


def _deterministic_embedder(dim: int = 8):
    """Return a mock embedder that produces deterministic vectors from text hashes."""
    import hashlib

    def embed(texts: List[str]) -> List[List[float]]:
        result = []
        for text in texts:
            h = hashlib.md5(text.encode()).hexdigest()
            raw = [int(h[i * 2 : i * 2 + 2], 16) / 255.0 for i in range(dim)]
            norm = math.sqrt(sum(x * x for x in raw))
            result.append([x / norm if norm else 0.0 for x in raw])
        return result

    return embed


def _index_repo(tmp_path: Path, files: dict[str, str]) -> str:
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


_SAMPLE_FILES = {
    "app.py": "from helpers import fmt\n\ndef greet(name):\n    return fmt(name)\n",
    "helpers.py": "def fmt(name):\n    return f'Hello, {name}'\n",
    "tests/test_app.py": "from app import greet\n\ndef test_greet():\n    assert greet('x')\n",
}


class TestEmbeddingCompute:
    def test_compute_embeds_symbols(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        svc = EmbeddingService(db, _embed_fn=_deterministic_embedder())
        result = svc.compute()
        assert result.command == "embeddings"
        assert result.action == "compute"
        assert result.nodes_embedded > 0
        assert result.nodes_skipped == 0

    def test_compute_caches_on_rerun(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        svc = EmbeddingService(db, _embed_fn=_deterministic_embedder())
        r1 = svc.compute()
        r2 = svc.compute()
        assert r2.nodes_cached == r1.nodes_embedded
        assert r2.nodes_embedded == 0

    def test_compute_empty_repo(self, tmp_path):
        repo = tmp_path / "empty"
        repo.mkdir()
        db = str(tmp_path / "index.db")
        IndexService(db).index(str(repo), profile="small")
        svc = EmbeddingService(db, _embed_fn=_deterministic_embedder())
        result = svc.compute()
        assert result.nodes_embedded == 0
        assert len(result.warnings) > 0

    def test_compute_stores_vectors_in_db(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        svc = EmbeddingService(db, _embed_fn=_deterministic_embedder())
        svc.compute()

        from csegraph._core.index.repository import ProjectIndex

        index = ProjectIndex(db)
        try:
            index.initialize_schema()
            rows = index.conn.execute("SELECT * FROM embedding_cache").fetchall()
            assert len(rows) > 0
            for row in rows:
                assert row["model"] == "local:all-MiniLM-L6-v2"
                vec = _blob_to_vector(row["vector"])
                assert len(vec) == 8
        finally:
            index.close()

    def test_compute_provider_identity_overwrites(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        svc1 = EmbeddingService(db, model="model-a", _embed_fn=_deterministic_embedder())
        svc2 = EmbeddingService(db, model="model-b", _embed_fn=_deterministic_embedder())
        svc1.compute()
        svc2.compute()

        from csegraph._core.index.repository import ProjectIndex

        index = ProjectIndex(db)
        try:
            index.initialize_schema()
            models = set()
            for row in index.conn.execute("SELECT model FROM embedding_cache"):
                models.add(row["model"])
            assert "local:model-b" in models
            assert "local:model-a" not in models
        finally:
            index.close()

    def test_compute_same_model_caches(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        svc = EmbeddingService(db, model="test-model", _embed_fn=_deterministic_embedder())
        r1 = svc.compute()
        r2 = svc.compute()
        assert r1.nodes_embedded > 0
        assert r2.nodes_cached == r1.nodes_embedded
        assert r2.nodes_embedded == 0
        assert r1.model == "local:test-model"


class TestEmbeddingSearch:
    def test_search_returns_results(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        embedder = _deterministic_embedder()
        svc = EmbeddingService(db, _embed_fn=embedder)
        svc.compute()
        result = svc.search("greeting function", top_k=5, hybrid=False)
        assert result.action == "search"
        assert result.query == "greeting function"
        assert len(result.hits) > 0

    def test_search_hybrid(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        embedder = _deterministic_embedder()
        svc = EmbeddingService(db, _embed_fn=embedder)
        svc.compute()
        result = svc.search("greet", top_k=5, hybrid=True)
        assert result.action == "search"
        assert len(result.hits) > 0

    def test_search_no_embeddings_warns(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        svc = EmbeddingService(db, _embed_fn=_deterministic_embedder())
        result = svc.search("greet", hybrid=False)
        assert any("compute" in w.lower() or "no embedding" in w.lower() for w in result.warnings)

    def test_search_top_k_limits_results(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        svc = EmbeddingService(db, _embed_fn=_deterministic_embedder())
        svc.compute()
        result = svc.search("function", top_k=2, hybrid=False)
        assert len(result.hits) <= 2

    def test_search_hit_fields(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        svc = EmbeddingService(db, _embed_fn=_deterministic_embedder())
        svc.compute()
        result = svc.search("fmt", top_k=5, hybrid=False)
        for hit in result.hits:
            assert hit.node_id
            assert hit.name
            assert hit.kind
            assert hit.path
            assert isinstance(hit.score, float)
            assert hit.source in ("embedding", "hybrid", "fts")


class TestEmbeddingStatus:
    def test_status_shows_count(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        svc = EmbeddingService(db, _embed_fn=_deterministic_embedder())
        svc.compute()
        result = svc.status()
        assert result.action == "status"
        assert result.nodes_embedded > 0

    def test_status_empty(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        svc = EmbeddingService(db, _embed_fn=_deterministic_embedder())
        result = svc.status()
        assert result.nodes_embedded == 0


class TestEmbeddingClear:
    def test_clear_removes_vectors(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        svc = EmbeddingService(db, _embed_fn=_deterministic_embedder())
        svc.compute()
        r1 = svc.status()
        assert r1.nodes_embedded > 0
        svc.clear()
        r2 = svc.status()
        assert r2.nodes_embedded == 0

    def test_clear_only_affects_current_model(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        svc = EmbeddingService(db, _embed_fn=_deterministic_embedder())
        svc.compute()
        before = svc.status().nodes_embedded
        assert before > 0
        result = svc.clear()
        assert result.nodes_embedded == before
        assert svc.status().nodes_embedded == 0


class TestVectorHelpers:
    def test_vector_roundtrip(self):
        vec = [0.1, 0.2, 0.3, 0.4]
        blob = _vector_to_blob(vec)
        recovered = _blob_to_vector(blob)
        for a, b in zip(vec, recovered, strict=True):
            assert abs(a - b) < 1e-6

    def test_cosine_identical(self):
        v = [1.0, 0.0, 0.0]
        assert abs(_cosine_similarity(v, v) - 1.0) < 1e-6

    def test_cosine_orthogonal(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert abs(_cosine_similarity(a, b)) < 1e-6

    def test_cosine_zero_vector(self):
        a = [0.0, 0.0]
        b = [1.0, 0.0]
        assert _cosine_similarity(a, b) == 0.0


class TestRRFMerge:
    def test_rrf_merge_combines_rankings(self):
        r1 = [("a", 0.9), ("b", 0.8), ("c", 0.7)]
        r2 = [("b", 0.95), ("c", 0.85), ("a", 0.75)]
        merged = _rrf_merge(r1, r2)
        ids = [nid for nid, _ in merged]
        assert "a" in ids
        assert "b" in ids
        assert "c" in ids
        assert merged[0][0] == "b"

    def test_rrf_merge_single_ranking(self):
        r1 = [("x", 1.0), ("y", 0.5)]
        merged = _rrf_merge(r1)
        assert merged[0][0] == "x"
        assert merged[1][0] == "y"


class TestEmbeddingProviders:
    def test_providers_constant(self):
        assert "local" in EMBEDDING_PROVIDERS
        assert "openai-compatible" in EMBEDDING_PROVIDERS

    def test_invalid_provider_raises(self, tmp_path):
        db = _index_repo(tmp_path, {"a.py": "x = 1\n"})
        with pytest.raises(ValueError, match="Unknown provider"):
            EmbeddingService(db, provider="bad")

    def test_openai_without_endpoint_raises(self, tmp_path):
        db = _index_repo(tmp_path, {"a.py": "def f(): pass\n"})
        svc = EmbeddingService(db, provider="openai-compatible")
        with pytest.raises(ValueError, match="endpoint"):
            svc.compute()


class TestEmbeddingSerialization:
    def test_result_serializable(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        svc = EmbeddingService(db, _embed_fn=_deterministic_embedder())
        result = svc.compute()
        payload = to_dict(result)
        assert isinstance(json.dumps(payload), str)

    def test_search_result_serializable(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        svc = EmbeddingService(db, _embed_fn=_deterministic_embedder())
        svc.compute()
        result = svc.search("greet", top_k=3, hybrid=False)
        payload = to_dict(result)
        text = json.dumps(payload)
        assert "hits" in text


class TestEmbeddingMCP:
    def test_tool_is_cli_only(self):
        from csegraph._core.server.app import _handle_tool

        with pytest.raises(ValueError, match="Unknown tool"):
            _handle_tool("csegraph_embeddings", {})

    def test_prompt_is_not_agent_facing(self):
        from csegraph._core.server.app import _handle_prompt

        with pytest.raises(ValueError, match="Unknown prompt"):
            _handle_prompt("csegraph-embeddings", {"repo": "/repo", "action": "compute"})
