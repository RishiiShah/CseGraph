"""Backward-compatible re-exports.

Canonical home: csegraph.languages.python.parser. Node-ID helpers live in
csegraph.core.ids. New code should import from those modules; this shim
exists so external callers (and the legacy `agents/` wrappers added in
Phase 3) keep working unchanged.
"""

from csegraph.core.ids import file_node_id, symbol_node_id
from csegraph.languages.python.parser import (
    EXCLUDED_DIRS,
    ParsedFile,
    ParsedSymbol,
    code_tokenize,
    extract_called_symbols,
    extract_query_entities,
    iter_python_files,
    module_name_from_relpath,
    parse_python_file,
    resolve_local_import,
    sha256_file,
    sha256_text,
    to_repo_relative,
)

__all__ = [
    "EXCLUDED_DIRS",
    "ParsedFile",
    "ParsedSymbol",
    "code_tokenize",
    "extract_called_symbols",
    "extract_query_entities",
    "file_node_id",
    "iter_python_files",
    "module_name_from_relpath",
    "parse_python_file",
    "resolve_local_import",
    "sha256_file",
    "sha256_text",
    "symbol_node_id",
    "to_repo_relative",
]
