from __future__ import annotations

from csegraph_core.languages.base import Tokenizer
from csegraph_core.languages.python.tokenizer import code_tokenize
from csegraph_core.text.query_tokenizer import QueryTokenizer, query_tokenizer

_CORPUS = [
    "Implement shortest_path for graph_analytics module",
    "Fix the bug in UserService.create_user method",
    "Add logging to buildReport function",
    "Refactor DataProcessor class to use registry pattern",
    "Write tests for extract_query_entities in text.entities",
    "Update the import resolution logic in PythonParser",
    "Optimize FTS5 BM25 scoring weights",
    "Remove deprecated parse_python_file standalone function",
    "Add NOT NULL constraint on language column in nodes table",
]


def test_query_tokenizer_matches_code_tokenize():
    qt = QueryTokenizer()
    for text in _CORPUS:
        assert qt.tokenize(text) == code_tokenize(text), f"drift on: {text!r}"


def test_module_singleton_matches_code_tokenize():
    for text in _CORPUS:
        assert query_tokenizer.tokenize(text) == code_tokenize(text), f"drift on: {text!r}"


def test_query_tokenizer_conforms_to_tokenizer_protocol():
    assert isinstance(query_tokenizer, Tokenizer)


def test_query_tokenizer_splits_camel_case():
    tokens = query_tokenizer.tokenize("buildReport")
    assert "build" in tokens
    assert "report" in tokens


def test_query_tokenizer_drops_stop_words():
    tokens = query_tokenizer.tokenize("iterate the list")
    assert "the" not in tokens
    assert "iterate" in tokens
