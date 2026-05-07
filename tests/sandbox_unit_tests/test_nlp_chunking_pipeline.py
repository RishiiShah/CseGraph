"""Unit tests for the nlp_chunking_pipeline sandbox."""
import os
import sys

SANDBOX_PATH = os.environ.get(
    "SANDBOX_PATH",
    os.path.join(os.path.dirname(__file__), "..", "..", "sandboxes", "nlp_chunking_pipeline"),
)
sys.path.insert(0, os.path.abspath(SANDBOX_PATH))

from pipeline.chunker import SentenceChunker
from pipeline.embedder import HashEmbedder
from pipeline.retriever import TopKRetriever
from pipeline.orchestrator import RAGPipeline


# ---------------------------------------------------------------------------
# SentenceChunker
# ---------------------------------------------------------------------------

def test_chunk_splits_on_period():
    chunker = SentenceChunker()
    result = chunker.chunk("Hello world. How are you.")
    assert len(result) == 2


def test_chunk_three_sentences():
    chunker = SentenceChunker()
    result = chunker.chunk("First. Second. Third.")
    assert len(result) == 3


def test_chunk_no_period_returns_single():
    chunker = SentenceChunker()
    result = chunker.chunk("No period here")
    assert result == ["No period here"]


def test_chunk_strips_whitespace_from_parts():
    chunker = SentenceChunker()
    result = chunker.chunk("  Hello.  World.  ")
    assert all(s == s.strip() for s in result)


def test_chunk_filters_empty_parts():
    chunker = SentenceChunker()
    result = chunker.chunk("Hello. . World.")
    assert "" not in result


def test_chunk_content_is_correct():
    chunker = SentenceChunker()
    result = chunker.chunk("Alpha. Beta.")
    assert "Alpha" in result
    assert "Beta" in result


# ---------------------------------------------------------------------------
# HashEmbedder
# ---------------------------------------------------------------------------

def test_embed_returns_one_vector_per_chunk():
    embedder = HashEmbedder()
    chunks = ["hello world", "foo bar baz"]
    vectors = embedder.embed(chunks)
    assert len(vectors) == 2


def test_embed_vector_has_two_dimensions():
    embedder = HashEmbedder()
    vectors = embedder.embed(["hello world"])
    assert len(vectors[0]) == 2


def test_embed_empty_input_returns_empty():
    embedder = HashEmbedder()
    assert embedder.embed([]) == []


def test_embed_values_are_floats():
    embedder = HashEmbedder()
    vectors = embedder.embed(["test chunk"])
    assert all(isinstance(v, float) for v in vectors[0])


# ---------------------------------------------------------------------------
# TopKRetriever
# ---------------------------------------------------------------------------

def test_top_k_returns_at_most_k_chunks():
    retriever = TopKRetriever()
    chunks = ["alpha beta", "gamma delta", "epsilon zeta"]
    vectors = [[0.1, 0.1], [0.2, 0.2], [0.3, 0.3]]
    result = retriever.top_k(chunks, vectors, "alpha", k=2)
    assert len(result) <= 2


def test_top_k_prefers_query_matching_chunk():
    retriever = TopKRetriever()
    chunks = ["completely unrelated text", "alpha is the query term"]
    vectors = [[0.1, 0.1], [0.1, 0.1]]
    result = retriever.top_k(chunks, vectors, "alpha", k=1)
    assert result[0] == "alpha is the query term"


def test_top_k_returns_list_of_strings():
    retriever = TopKRetriever()
    chunks = ["a b c"]
    vectors = [[0.1, 0.1]]
    result = retriever.top_k(chunks, vectors, "a", k=1)
    assert isinstance(result, list)
    assert all(isinstance(s, str) for s in result)


# ---------------------------------------------------------------------------
# RAGPipeline
# ---------------------------------------------------------------------------

def test_rag_pipeline_returns_chunk_count():
    pipeline = RAGPipeline()
    result = pipeline.retrieve("First sentence. Second sentence. Third sentence.", "first", k=2)
    assert "chunk_count" in result
    assert result["chunk_count"] == 3


def test_rag_pipeline_returns_selected():
    pipeline = RAGPipeline()
    result = pipeline.retrieve("Alpha. Beta.", "Alpha", k=1)
    assert "selected" in result
    assert len(result["selected"]) == 1


def test_rag_pipeline_k_limits_selected():
    pipeline = RAGPipeline()
    result = pipeline.retrieve("A. B. C. D. E.", "A", k=2)
    assert len(result["selected"]) <= 2


def test_rag_pipeline_single_sentence():
    pipeline = RAGPipeline()
    result = pipeline.retrieve("Only one chunk here", "chunk", k=1)
    assert result["chunk_count"] == 1
    assert len(result["selected"]) == 1
