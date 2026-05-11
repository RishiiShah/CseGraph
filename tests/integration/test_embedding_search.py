from __future__ import annotations

import struct
from pathlib import Path

import pytest

from csegraph_core.embeddings.encoder import (
    EMBEDDING_DIM,
    blob_to_vector,
    build_embedding_text,
    vector_to_blob,
)


def _has_sentence_transformers() -> bool:
    try:
        import sentence_transformers  # noqa: F401
        return True
    except ImportError:
        return False


requires_embeddings = pytest.mark.skipif(
    not _has_sentence_transformers(),
    reason="sentence-transformers not installed",
)

requires_no_embeddings = pytest.mark.skipif(
    _has_sentence_transformers(),
    reason="sentence-transformers is installed",
)


def _write_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "geometry.py").write_text(
        'def calculate_area(radius: float) -> float:\n    """Compute the area of a circle."""\n    import math\n    return math.pi * radius ** 2\n',
        encoding="utf-8",
    )
    (root / "greet.py").write_text(
        'def say_hello(name: str) -> str:\n    """Return a greeting string."""\n    return f"Hello, {name}!"\n',
        encoding="utf-8",
    )


class TestVectorSerialization:
    def test_roundtrip_blob(self):
        original = [float(i) / 100 for i in range(EMBEDDING_DIM)]
        blob = vector_to_blob(original)
        assert len(blob) == EMBEDDING_DIM * 4
        restored = blob_to_vector(blob)
        assert len(restored) == EMBEDDING_DIM
        for a, b in zip(original, restored):
            assert abs(a - b) < 1e-6

    def test_build_embedding_text_with_signature_and_docstring(self):
        text = build_embedding_text("def foo(x)", "Does foo things", "foo")
        assert "def foo(x)" in text
        assert "Does foo things" in text

    def test_build_embedding_text_fallback_to_name(self):
        text = build_embedding_text(None, None, "my_func")
        assert text == "my_func"

    def test_build_embedding_text_signature_only(self):
        text = build_embedding_text("def bar()", None, "bar")
        assert text == "def bar()"


@requires_embeddings
class TestEmbeddingIndex:
    def test_index_populates_embedding_cache(self, tmp_path):
        from csegraph import IndexService
        from csegraph_core.index.repository import ProjectIndex

        repo = tmp_path / "repo"
        db_path = tmp_path / "index.db"
        _write_repo(repo)
        result = IndexService(db_path).index(repo, profile="small")

        assert result.embeddings_indexed > 0

        index = ProjectIndex(db_path)
        try:
            rows = index.conn.execute("SELECT node_id, vector FROM embedding_cache").fetchall()
            assert len(rows) > 0
            for row in rows:
                vec = blob_to_vector(row["vector"])
                assert len(vec) == EMBEDDING_DIM
        finally:
            index.close()

    def test_no_embed_flag_skips_embeddings(self, tmp_path):
        from csegraph import IndexService
        from csegraph_core.index.repository import ProjectIndex

        repo = tmp_path / "repo"
        db_path = tmp_path / "index.db"
        _write_repo(repo)
        result = IndexService(db_path).index(repo, profile="small", embed=False)

        assert result.embeddings_indexed == 0

        index = ProjectIndex(db_path)
        try:
            count = index.conn.execute("SELECT COUNT(*) AS c FROM embedding_cache").fetchone()["c"]
            assert count == 0
        finally:
            index.close()

    def test_refresh_updates_embeddings(self, tmp_path):
        from csegraph import IndexService, RefreshService
        from csegraph_core.index.repository import ProjectIndex

        repo = tmp_path / "repo"
        db_path = tmp_path / "index.db"
        _write_repo(repo)
        IndexService(db_path).index(repo, profile="small")

        (repo / "geometry.py").write_text(
            'def calculate_area(radius: float) -> float:\n    """Compute area of a circle given its radius."""\n    import math\n    return math.pi * radius ** 2\n',
            encoding="utf-8",
        )
        refresh_result = RefreshService(db_path).refresh(profile="small")
        assert refresh_result.embeddings_indexed > 0

    def test_context_includes_embedding_match(self, tmp_path):
        from csegraph import ContextService, IndexService

        repo = tmp_path / "repo"
        db_path = tmp_path / "index.db"
        _write_repo(repo)
        IndexService(db_path).index(repo, profile="small")

        result = ContextService(db_path).build_context(
            task="compute the area of a circle",
            profile="small",
            explain=True,
        )
        all_evidence = []
        all_reasons = []
        for node in result.context_nodes:
            all_evidence.extend(node.evidence)
            all_reasons.extend(node.reason)
        assert "embedding_match" in all_evidence or "embedding_match" in all_reasons


@requires_no_embeddings
class TestGracefulFallback:
    def test_index_succeeds_without_embeddings(self, tmp_path):
        from csegraph import IndexService

        repo = tmp_path / "repo"
        db_path = tmp_path / "index.db"
        _write_repo(repo)
        result = IndexService(db_path).index(repo, profile="small")
        assert result.embeddings_indexed == 0
        assert result.symbols_indexed > 0

    def test_context_works_without_embeddings(self, tmp_path):
        from csegraph import ContextService, IndexService

        repo = tmp_path / "repo"
        db_path = tmp_path / "index.db"
        _write_repo(repo)
        IndexService(db_path).index(repo, profile="small")
        result = ContextService(db_path).build_context(
            task="compute the area of a circle",
            profile="small",
        )
        assert len(result.context_nodes) > 0
