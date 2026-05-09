"""Adapters between csegraph dataclasses and the legacy Pydantic schemas in models/.

The original research pipeline (agents/, models/) uses Pydantic models that
serialize to specific JSON shapes consumed by compare_baselines, report_plots,
and the cse / compression / code_gen agents. The csegraph core uses
dataclasses internally for speed and SQLite friendliness. These adapters
bridge the two so agents/ can be a thin wrapper around the shared parser
without breaking JSON byte-stability.

Round-trip tests for these conversions live in
tests/test_legacy_adapters.py (added in Phase 3).
"""

from __future__ import annotations

from typing import Dict, List

from csegraph_core.languages.types import ParsedFile, ParsedSymbol
from models.code_element import CodeNode, FileNode, MethodNode


def parsed_file_to_filenode(parsed: ParsedFile) -> FileNode:
    """Convert a csegraph ParsedFile (shared parser output) into the legacy
    Pydantic FileNode. Preserves the exact field semantics of the original
    IngestionAgent: absolute file paths, sorted unique imports, top-level
    function/class symbols only, methods nested under their class as
    MethodNode children with short (parent-stripped) names.
    """
    abs_path = parsed.abs_path

    methods_by_parent: Dict[str, List[ParsedSymbol]] = {}
    for symbol in parsed.symbols:
        if symbol.kind == "method" and symbol.parent_symbol_id:
            methods_by_parent.setdefault(symbol.parent_symbol_id, []).append(symbol)

    nodes: List[CodeNode] = []
    for symbol in parsed.symbols:
        if symbol.kind not in ("function", "class"):
            continue

        children: List[MethodNode] = []
        if symbol.kind == "class":
            for method in methods_by_parent.get(symbol.node_id, []):
                short_name = method.name.rsplit(".", 1)[-1]
                children.append(
                    MethodNode(
                        name=short_name,
                        start_line=method.start_line,
                        end_line=method.end_line,
                        docstring=method.docstring or None,
                        code_content=method.source,
                    )
                )

        nodes.append(
            CodeNode(
                name=symbol.name,
                node_type=symbol.kind,
                file_path=abs_path,
                start_line=symbol.start_line,
                end_line=symbol.end_line,
                docstring=symbol.docstring or None,
                code_content=symbol.source,
                children=children,
            )
        )

    return FileNode(
        file_path=abs_path,
        imports=list(parsed.imports),
        nodes=nodes,
    )
