from typing import Protocol


class Chunker(Protocol):
    def chunk(self, text: str) -> list[str]:
        """Split input text into chunks."""


class Embedder(Protocol):
    def embed(self, chunks: list[str]) -> list[list[float]]:
        """Transform chunks into vector representations."""


class Retriever(Protocol):
    def top_k(self, chunks: list[str], vectors: list[list[float]], query: str, k: int) -> list[str]:
        """Return top-k chunks for the query."""
