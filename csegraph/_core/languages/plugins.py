from __future__ import annotations

from typing import Optional

from csegraph._core.languages.base import DefaultTokenizer, Parser, Tokenizer
from csegraph._core.languages.registry import registry
from csegraph._core.languages.treesitter.config import LanguageConfig
from csegraph._core.languages.treesitter.parser import TreeSitterParser


def register_parser(parser: Parser, tokenizer: Optional[Tokenizer] = None) -> Parser:
    """Register a custom parser with the process-local language registry."""
    _validate_parser(parser)
    registry.register(parser, tokenizer or DefaultTokenizer())
    return parser


def register_tree_sitter_language(
    config: LanguageConfig,
    tokenizer: Optional[Tokenizer] = None,
) -> TreeSitterParser:
    """Register a tree-sitter language config as a custom parser."""
    parser = TreeSitterParser(config)
    register_parser(parser, tokenizer=tokenizer)
    return parser


def _validate_parser(parser: Parser) -> None:
    if not getattr(parser, "language", ""):
        raise ValueError("Custom parser must define a non-empty language name.")
    if not getattr(parser, "extensions", ()):
        raise ValueError("Custom parser must define at least one file extension.")


__all__ = [
    "register_parser",
    "register_tree_sitter_language",
]
