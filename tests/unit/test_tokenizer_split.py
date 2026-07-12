from __future__ import annotations

import pytest

from csegraph._core.languages.base import DefaultTokenizer
from csegraph._core.languages.registry import UnsupportedLanguageError
from csegraph._core.text.tokens import code_tokenize


def test_code_tokenize_splits_camel_case():
    tokens = code_tokenize("buildReport")
    assert "build" in tokens
    assert "report" in tokens


def test_code_tokenize_drops_stop_words():
    tokens = code_tokenize("iterate the list and items")
    assert "the" not in tokens
    assert "and" not in tokens
    assert "iterate" in tokens
    assert "list" in tokens


def test_code_tokenize_drops_py_stop_word():
    tokens = code_tokenize("parse py file")
    assert "py" not in tokens


def test_default_tokenizer_matches_code_tokenize():
    tokenizer = DefaultTokenizer()
    text = "BuildReportFromUser snake_case_name"
    assert tokenizer.tokenize(text) == code_tokenize(text)


def test_tokenizer_for_python_returns_default_tokenizer():
    from csegraph._core.languages import registry

    tokenizer = registry.tokenizer_for("python")
    assert isinstance(tokenizer, DefaultTokenizer)


def test_tokenizer_for_unknown_raises():
    from csegraph._core.languages import registry

    with pytest.raises(UnsupportedLanguageError):
        registry.tokenizer_for("javascript")
