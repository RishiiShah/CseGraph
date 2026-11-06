"""Token-efficient prose for agent-facing output.

ATSE-1000 (``terse_prose``) compresses human-readable prose without touching
code, paths, symbols, or identifiers. ATE-1000 (``compact_internal``) is the
more aggressive profile used for stored/internal text that users never see.
"""

from __future__ import annotations

import json
import re
from typing import Any

_PROTECTED_PATTERN = re.compile(
    r"("
    r"```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`]*`"
    r"|(?:https?|file)://\S+"
    r"|(?<!\w)'(?:\\.|[^'\\])*'(?!\w)"
    r'|(?<!\w)"(?:\\.|[^"\\])*"(?!\w)'
    r"|[A-Za-z_][A-Za-z0-9_]*(?:::|\.|/)[A-Za-z0-9_./:+-]+"
    r"|[A-Za-z_][A-Za-z0-9_]*_[A-Za-z0-9_]+"
    r"|[A-Za-z](?=\s*[,)\]])"
    r")"
)

_TERSE_PHRASES = (
    ("has been", ""),
    ("have been", ""),
    ("is required", "required"),
    ("are required", "required"),
    ("in order to", "to"),
    ("make sure to", ""),
    ("please", ""),
    ("you should", ""),
    ("this is", ""),
    ("there is", ""),
    ("there are", ""),
)

_TERSE_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "just",
        "really",
        "quite",
        "very",
        "simply",
        "please",
        "actually",
        "basically",
        "currently",
        "essentially",
    }
)

_INTERNAL_WORDS = frozenset(
    {
        "an",
        "the",
        "just",
        "really",
        "quite",
        "very",
        "simply",
        "please",
        "actually",
        "basically",
        "currently",
        "essentially",
        "and",
        "or",
        "but",
        "to",
        "of",
        "in",
        "on",
        "for",
        "with",
        "by",
        "as",
        "at",
        "from",
        "into",
        "that",
        "this",
        "these",
        "those",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "it",
        "its",
        "will",
        "would",
        "can",
        "could",
        "should",
    }
)

_WORD_PATTERN = re.compile(r"\b[A-Za-z][A-Za-z'-]*\b")
_WHITESPACE_PATTERN = re.compile(r"\s+")
_PUNCTUATION_GAP_PATTERN = re.compile(r"\s+([,.;:!?])")


def terse_prose(text: Any) -> str:
    """ATSE-1000: telegraphic prose; code and identifiers stay byte-identical."""

    return _compress(str(text or ""), stopwords=_TERSE_WORDS, aggressive=False)


def compact_internal(text: Any) -> str:
    """ATE-1000: hidden storage compaction for summaries and internal text."""

    return _compress(str(text or ""), stopwords=_INTERNAL_WORDS, aggressive=True)


def compact_json(value: Any) -> str:
    """ATE-1000: canonical, separator-minimal JSON for internal storage."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _compress(text: str, *, stopwords: frozenset[str], aggressive: bool) -> str:
    stripped = text.strip()
    if not stripped:
        return ""

    parts = _PROTECTED_PATTERN.split(stripped)
    for index in range(0, len(parts), 2):
        segment = _compress_segment(parts[index], stopwords)
        segment = _WHITESPACE_PATTERN.sub(" ", segment)
        parts[index] = _PUNCTUATION_GAP_PATTERN.sub(r"\1", segment)
    if aggressive:
        parts[-1] = parts[-1].rstrip(" .,;:")
    return "".join(parts).strip()


def _compress_segment(segment: str, stopwords: frozenset[str]) -> str:
    for old, new in _TERSE_PHRASES:
        segment = re.sub(
            r"\b" + re.escape(old) + r"\b",
            new,
            segment,
            flags=re.IGNORECASE,
        )
    segment = _WORD_PATTERN.sub(
        lambda match: "" if match.group(0).casefold() in stopwords else match.group(0),
        segment,
    )
    return segment


__all__ = ["compact_internal", "compact_json", "terse_prose"]
