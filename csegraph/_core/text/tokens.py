"""Shared tokenization kernel used by both source-side and query-side tokenizers."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Set

_STOP_WORDS = {
    "in", "on", "by", "to", "of", "at", "is", "it", "or", "an", "do", "be",
    "no", "up", "as", "if", "so", "we", "my", "py", "the", "and", "for", "with",
    "from", "that", "this", "into", "are", "was", "has", "had", "not", "its",
}

_RE_CAMEL_SPLIT = re.compile(r"([a-z0-9])([A-Z])")
_RE_ACRONYM_SPLIT = re.compile(r"([A-Z]{2,})([A-Z][a-z])")
_RE_NON_ALNUM = re.compile(r"[^a-zA-Z0-9]+")


def _default_text_tokenize(text: str) -> List[str]:
    text = _RE_CAMEL_SPLIT.sub(r"\1 \2", text)
    text = _RE_ACRONYM_SPLIT.sub(r"\1 \2", text)
    text = _RE_NON_ALNUM.sub(" ", text)
    return [token.lower() for token in text.split() if len(token) > 1 and token.lower() not in _STOP_WORDS]


def code_tokenize(text: str) -> List[str]:
    """Tokenize text with code-identifier awareness.

    Splits CamelCase, snake_case, dotted names into sub-tokens.
    Filters stop words and single-character tokens.
    """
    return _default_text_tokenize(text)


def tokenize_node_content(
    node_id: str,
    row: Dict[str, Any],
    summaries: Dict[str, str],
    language_registry: Any,
) -> Set[str]:
    content = " ".join(
        [
            row["name"],
            row["file_path"],
            row.get("signature") or "",
            row.get("docstring") or "",
            summaries.get(node_id, ""),
        ]
    )
    source_tokenizer = language_registry.tokenizer_for(row["language"])
    return set(source_tokenizer.tokenize(content))
