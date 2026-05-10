"""Shared tokenization kernel used by both source-side and query-side tokenizers."""
from __future__ import annotations

import re
from typing import List

_STOP_WORDS = {
    "in", "on", "by", "to", "of", "at", "is", "it", "or", "an", "do", "be",
    "no", "up", "as", "if", "so", "we", "my", "py", "the", "and", "for", "with",
    "from", "that", "this", "into", "are", "was", "has", "had", "not", "its",
}


def _default_text_tokenize(text: str) -> List[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = re.sub(r"([A-Z]{2,})([A-Z][a-z])", r"\1 \2", text)
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text)
    return [token.lower() for token in text.split() if len(token) > 1 and token.lower() not in _STOP_WORDS]


def code_tokenize(text: str) -> List[str]:
    """Tokenize text with code-identifier awareness.

    Splits CamelCase, snake_case, dotted names into sub-tokens.
    Filters stop words and single-character tokens.
    """
    return _default_text_tokenize(text)
