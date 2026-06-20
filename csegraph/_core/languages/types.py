"""Language-neutral parsed output types.

These dataclasses represent the output of any Parser implementation and are
intentionally not tied to any specific language. They live here so
Parser.parse() can reference them without importing from a language-specific module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ParsedImport:
    name: str
    start_line: int
    end_line: int
    source: str
    resolved_file_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedReference:
    kind: str
    name: str
    start_line: int
    end_line: int
    source: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedSymbol:
    node_id: str
    kind: str
    name: str
    file_path: str
    start_line: int
    end_line: int
    signature: str
    docstring: str
    source: str
    source_hash: str
    parent_symbol_id: Optional[str] = None
    calls: List[str] = field(default_factory=list)
    bases: List[str] = field(default_factory=list)
    decorators: List[str] = field(default_factory=list)
    references: List[ParsedReference] = field(default_factory=list)
    is_test: bool = False


@dataclass
class ParsedFile:
    rel_path: str
    abs_path: str
    sha256: str
    mtime: float
    size: int
    language: str = "python"
    parse_status: str = "ok"
    parse_error: Optional[str] = None
    imports: List[str] = field(default_factory=list)
    import_records: List[ParsedImport] = field(default_factory=list)
    symbols: List[ParsedSymbol] = field(default_factory=list)
