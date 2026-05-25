"""Optional local-first embedding service for semantic code search."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import struct
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

from csegraph_core.core.models import EmbeddingResult, EmbeddingSearchHit
from csegraph_core.index.repository import ProjectIndex

EMBEDDING_PROVIDERS = ("local", "openai-compatible")
DEFAULT_LOCAL_MODEL = "all-MiniLM-L6-v2"
DEFAULT_OPENAI_MODEL = "text-embedding-3-small"
RRF_K = 60


class EmbeddingService:
    def __init__(
        self,
        db_path: str | Path,
        *,
        model: str | None = None,
        provider: str = "local",
        endpoint: str | None = None,
        _embed_fn: Callable[[List[str]], List[List[float]]] | None = None,
    ):
        self.db_path = str(Path(db_path))
        self.provider = provider
        self.endpoint = endpoint
        self._embed_fn = _embed_fn

        if provider == "local":
            self.model = model or DEFAULT_LOCAL_MODEL
        elif provider == "openai-compatible":
            self.model = model or DEFAULT_OPENAI_MODEL
        else:
            raise ValueError(
                f"Unknown provider '{provider}'. Choose from: {', '.join(EMBEDDING_PROVIDERS)}"
            )

        self._model_identity = f"{self.provider}:{self.model}"

    def compute(
        self,
        *,
        node_ids: Sequence[str] | None = None,
    ) -> EmbeddingResult:
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            metadata = index.metadata()
            repo_root = metadata["root_dir"]
            embed = self._resolve_embedder()

            rows = _load_symbol_texts(index, node_ids)
            if not rows:
                return EmbeddingResult(
                    command="embeddings",
                    db_path=self.db_path,
                    repo_root=repo_root,
                    action="compute",
                    model=self._model_identity,
                    provider=self.provider,
                    warnings=["No symbols found to embed."],
                )

            cached = 0
            to_embed: List[Tuple[str, str, str]] = []

            for node_id, text, source_hash in rows:
                existing = index.conn.execute(
                    "SELECT model, source_hash FROM embedding_cache WHERE node_id = ?",
                    (node_id,),
                ).fetchone()
                if (
                    existing
                    and existing["model"] == self._model_identity
                    and existing["source_hash"] == source_hash
                ):
                    cached += 1
                else:
                    to_embed.append((node_id, text, source_hash))

            embedded = 0
            skipped = 0
            batch_size = 64

            for i in range(0, len(to_embed), batch_size):
                batch = to_embed[i : i + batch_size]
                texts = [t[1] for t in batch]
                try:
                    vectors = embed(texts)
                except Exception as exc:
                    logger.warning("Embedding batch %d-%d failed: %s", i, i + batch_size, exc)
                    skipped += len(batch)
                    continue

                if len(vectors) != len(batch):
                    skipped += len(batch)
                    continue

                now = time.time()
                for (node_id, _text, source_hash), vec in zip(batch, vectors):
                    blob = _vector_to_blob(vec)
                    index.conn.execute(
                        "INSERT OR REPLACE INTO embedding_cache "
                        "(node_id, model, source_hash, vector, updated_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (node_id, self._model_identity, source_hash, blob, now),
                    )
                    embedded += 1

            index.conn.commit()

            return EmbeddingResult(
                command="embeddings",
                db_path=self.db_path,
                repo_root=repo_root,
                action="compute",
                model=self._model_identity,
                provider=self.provider,
                nodes_embedded=embedded,
                nodes_skipped=skipped,
                nodes_cached=cached,
            )
        finally:
            index.close()

    def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        hybrid: bool = True,
    ) -> EmbeddingResult:
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            metadata = index.metadata()
            repo_root = metadata["root_dir"]
            warnings: List[str] = []

            embed = self._resolve_embedder()
            query_vecs = embed([query])
            if not query_vecs or not query_vecs[0]:
                return EmbeddingResult(
                    command="embeddings",
                    db_path=self.db_path,
                    repo_root=repo_root,
                    action="search",
                    model=self._model_identity,
                    provider=self.provider,
                    query=query,
                    top_k=top_k,
                    warnings=["Failed to embed query."],
                )

            query_vec = query_vecs[0]
            emb_ranked = _embedding_search(index, query_vec, self._model_identity)

            if hybrid:
                fts_ranked = _fts_search(index, query)
                merged = _rrf_merge(emb_ranked, fts_ranked)
                source = "hybrid"
            else:
                merged = emb_ranked
                source = "embedding"

            if not emb_ranked and hybrid and fts_ranked:
                warnings.append("No embedding results; fell back to FTS only.")
                source = "fts"
            elif not emb_ranked and not hybrid:
                warnings.append(
                    "No embedding results found. Run "
                    "'env/bin/python tools/csegraph_dev.py embeddings compute' first."
                )

            node_ids = [nid for nid, _ in merged[:top_k]]
            node_map = _load_node_info(index, node_ids)

            hits: List[EmbeddingSearchHit] = []
            for node_id, score in merged[:top_k]:
                info = node_map.get(node_id)
                if not info:
                    continue
                hits.append(EmbeddingSearchHit(
                    node_id=node_id,
                    name=info["name"],
                    kind=info["type"],
                    path=info["path"],
                    score=round(score, 4),
                    source=source,
                ))

            return EmbeddingResult(
                command="embeddings",
                db_path=self.db_path,
                repo_root=repo_root,
                action="search",
                model=self._model_identity,
                provider=self.provider,
                query=query,
                top_k=top_k,
                hits=hits,
                warnings=warnings,
            )
        finally:
            index.close()

    def status(self) -> EmbeddingResult:
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            metadata = index.metadata()
            repo_root = metadata["root_dir"]

            total = index.conn.execute(
                "SELECT COUNT(*) AS c FROM embedding_cache WHERE model = ?",
                (self._model_identity,),
            ).fetchone()["c"]

            stale = index.conn.execute(
                "SELECT COUNT(*) AS c FROM embedding_cache ec "
                "JOIN nodes n ON ec.node_id = n.id "
                "WHERE ec.model = ? AND ec.source_hash != n.source_hash",
                (self._model_identity,),
            ).fetchone()["c"]

            warnings = []
            if stale > 0:
                warnings.append(f"{stale} embedding(s) are stale — re-run compute to update.")

            return EmbeddingResult(
                command="embeddings",
                db_path=self.db_path,
                repo_root=repo_root,
                action="status",
                model=self._model_identity,
                provider=self.provider,
                nodes_embedded=total,
                nodes_skipped=stale,
                warnings=warnings,
            )
        finally:
            index.close()

    def clear(self) -> EmbeddingResult:
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            metadata = index.metadata()
            repo_root = metadata["root_dir"]

            deleted = index.conn.execute(
                "DELETE FROM embedding_cache WHERE model = ?",
                (self._model_identity,),
            ).rowcount
            index.conn.commit()

            return EmbeddingResult(
                command="embeddings",
                db_path=self.db_path,
                repo_root=repo_root,
                action="clear",
                model=self._model_identity,
                provider=self.provider,
                nodes_embedded=deleted,
            )
        finally:
            index.close()

    def _resolve_embedder(self) -> Callable[[List[str]], List[List[float]]]:
        if self._embed_fn is not None:
            return self._embed_fn

        if self.provider == "local":
            return _make_local_embedder(self.model)
        if self.provider == "openai-compatible":
            return _make_openai_embedder(self.model, self.endpoint)
        raise ValueError(f"Unknown provider: {self.provider}")


def _make_local_embedder(model_name: str) -> Callable[[List[str]], List[List[float]]]:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        raise ImportError(
            "Local embeddings require sentence-transformers. "
            "Install with: pip install sentence-transformers"
        )
    st_model = SentenceTransformer(model_name, trust_remote_code=False)

    def embed(texts: List[str]) -> List[List[float]]:
        vecs = st_model.encode(texts, show_progress_bar=False)
        return [v.tolist() for v in vecs]

    return embed


def _make_openai_embedder(
    model_name: str, endpoint: str | None
) -> Callable[[List[str]], List[List[float]]]:
    import os

    if not endpoint:
        raise ValueError(
            "openai-compatible provider requires --endpoint (e.g. http://localhost:11434/v1/embeddings)"
        )

    parsed = urlparse(endpoint)
    is_local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
    if not is_local and not os.environ.get("CSEGRAPH_ALLOW_CLOUD_EMBEDDINGS"):
        raise ValueError(
            "Non-localhost embedding endpoints require CSEGRAPH_ALLOW_CLOUD_EMBEDDINGS=1 env var. "
            "This is a safety check to prevent accidental cloud egress."
        )

    def embed(texts: List[str]) -> List[List[float]]:
        payload = json.dumps({"model": model_name, "input": texts}).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read())

        data = body.get("data", [])
        if len(data) != len(texts):
            raise ValueError(
                f"Embedding API returned {len(data)} vectors for {len(texts)} inputs"
            )

        indexed = all("index" in d for d in data)
        if indexed:
            indices = [d["index"] for d in data]
            if sorted(indices) != list(range(len(texts))):
                raise ValueError("Embedding API returned mismatched indices")
            data = sorted(data, key=lambda d: d["index"])

        return [d["embedding"] for d in data]

    return embed


def _load_symbol_texts(
    index: ProjectIndex,
    node_ids: Sequence[str] | None = None,
) -> List[Tuple[str, str, str]]:
    if node_ids is not None:
        if not node_ids:
            return []
        placeholders = ",".join("?" for _ in node_ids)
        rows = index.conn.execute(
            f"SELECT id, name, type, path, signature, docstring, source_hash "
            f"FROM nodes WHERE type IN ('class','function','method','test') "
            f"AND id IN ({placeholders})",
            list(node_ids),
        ).fetchall()
    else:
        rows = index.conn.execute(
            "SELECT id, name, type, path, signature, docstring, source_hash "
            "FROM nodes WHERE type IN ('class','function','method','test')"
        ).fetchall()

    result = []
    for row in rows:
        parts = [row["name"]]
        if row["signature"]:
            parts.append(row["signature"])
        if row["docstring"]:
            parts.append(row["docstring"])
        parts.append(f"{row['type']} in {row['path']}")
        text = " ".join(parts)
        result.append((row["id"], text, row["source_hash"]))
    return result


def _vector_to_blob(vec: List[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _blob_to_vector(blob: bytes) -> List[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _embedding_search(
    index: ProjectIndex,
    query_vec: List[float],
    model_identity: str,
) -> List[Tuple[str, float]]:
    rows = index.conn.execute(
        "SELECT node_id, vector FROM embedding_cache WHERE model = ?",
        (model_identity,),
    ).fetchall()

    scored: List[Tuple[str, float]] = []
    for row in rows:
        vec = _blob_to_vector(row["vector"])
        sim = _cosine_similarity(query_vec, vec)
        scored.append((row["node_id"], sim))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def _fts_search(
    index: ProjectIndex,
    query: str,
) -> List[Tuple[str, float]]:
    safe_query = query.replace('"', '""')
    tokens = safe_query.split()
    if not tokens:
        return []

    fts_query = " OR ".join(f'"{t}"' for t in tokens)
    try:
        rows = index.conn.execute(
            "SELECT node_id, rank FROM lexical_index WHERE lexical_index MATCH ? "
            "ORDER BY rank LIMIT 200",
            (fts_query,),
        ).fetchall()
    except Exception:
        logger.debug("FTS search failed", exc_info=True)
        return []

    return [(row["node_id"], -row["rank"]) for row in rows]


def _rrf_merge(
    *rankings: List[Tuple[str, float]],
) -> List[Tuple[str, float]]:
    scores: Dict[str, float] = {}
    for ranking in rankings:
        for rank_pos, (node_id, _) in enumerate(ranking, start=1):
            scores[node_id] = scores.get(node_id, 0.0) + 1.0 / (RRF_K + rank_pos)

    merged = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return merged


def _load_node_info(
    index: ProjectIndex,
    node_ids: List[str],
) -> Dict[str, Dict[str, Any]]:
    if not node_ids:
        return {}
    placeholders = ",".join("?" for _ in node_ids)
    rows = index.conn.execute(
        f"SELECT id, name, type, path FROM nodes WHERE id IN ({placeholders})",
        node_ids,
    ).fetchall()
    return {row["id"]: dict(row) for row in rows}
