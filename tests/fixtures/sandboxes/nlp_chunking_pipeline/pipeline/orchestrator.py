from .chunker import SentenceChunker
from .contracts import Chunker, Embedder, Retriever
from .embedder import HashEmbedder
from .retriever import TopKRetriever


class RAGPipeline:
    def __init__(
        self,
        chunker: Chunker | None = None,
        embedder: Embedder | None = None,
        retriever: Retriever | None = None,
    ):
        self.chunker = chunker or SentenceChunker()
        self.embedder = embedder or HashEmbedder()
        self.retriever = retriever or TopKRetriever()

    def retrieve(self, text: str, query: str, k: int = 2) -> dict:
        chunks = self.chunker.chunk(text)
        vectors = self.embedder.embed(chunks)
        selected = self.retriever.top_k(chunks, vectors, query, k)
        return {
            "chunk_count": len(chunks),
            "selected": selected,
        }


def run_demo(text: str, query: str) -> dict:
    return RAGPipeline().retrieve(text, query)
