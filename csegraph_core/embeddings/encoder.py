from __future__ import annotations

import struct
from typing import List, Optional

EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384

_model = None


def is_available() -> bool:
    try:
        import sentence_transformers  # noqa: F401
        return True
    except ImportError:
        return False


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(EMBEDDING_MODEL)
    return _model


def encode_texts(texts: List[str], batch_size: int = 64) -> List[List[float]]:
    model = _get_model()
    embeddings = model.encode(texts, batch_size=batch_size, show_progress_bar=False)
    return [row.tolist() for row in embeddings]


def encode_single(text: str) -> List[float]:
    return encode_texts([text])[0]


def vector_to_blob(vector: List[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def blob_to_vector(blob: bytes) -> List[float]:
    count = len(blob) // 4
    return list(struct.unpack(f"{count}f", blob))


def build_embedding_text(
    signature: Optional[str],
    docstring: Optional[str],
    name: str,
) -> str:
    parts = []
    if signature:
        parts.append(signature)
    if docstring:
        parts.append(docstring)
    if not parts:
        parts.append(name)
    return " ".join(parts)
