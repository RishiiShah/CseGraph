from __future__ import annotations

import ast
import hashlib
import os
import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set

from csegraph_core.core.ids import file_node_id, symbol_node_id

__all__ = [
    "EXCLUDED_DIRS",
    "ParsedSymbol",
    "ParsedFile",
    "file_node_id",
    "symbol_node_id",
    "code_tokenize",
    "sha256_text",
    "sha256_file",
    "iter_python_files",
    "to_repo_relative",
    "module_name_from_relpath",
    "parse_python_file",
    "resolve_local_import",
    "extract_called_symbols",
    "extract_query_entities",
]


EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    "node_modules",
    "venv",
    ".venv",
    "env",
    "site-packages",
}


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
    symbols: List[ParsedSymbol] = field(default_factory=list)


def code_tokenize(text: str) -> List[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = re.sub(r"([A-Z]{2,})([A-Z][a-z])", r"\1 \2", text)
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text)
    stop_words = {
        "in",
        "on",
        "by",
        "to",
        "of",
        "at",
        "is",
        "it",
        "or",
        "an",
        "do",
        "be",
        "no",
        "up",
        "as",
        "if",
        "so",
        "we",
        "my",
        "py",
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "into",
        "are",
        "was",
        "has",
        "had",
        "not",
        "its",
    }
    return [token.lower() for token in text.split() if len(token) > 1 and token.lower() not in stop_words]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_python_files(root_dir: Path) -> List[Path]:
    paths: List[Path] = []
    for root, dirs, files in os.walk(root_dir):
        dirs[:] = sorted(
            name for name in dirs if name not in EXCLUDED_DIRS and not name.startswith(".")
        )
        for filename in sorted(files):
            if filename.startswith(".") or not filename.endswith(".py"):
                continue
            paths.append(Path(root) / filename)
    return sorted(paths)


def to_repo_relative(path: Path, root_dir: Path) -> str:
    return path.resolve().relative_to(root_dir.resolve()).as_posix()


def module_name_from_relpath(rel_path: str) -> str:
    if rel_path.endswith("/__init__.py"):
        rel_path = rel_path[: -len("/__init__.py")]
    elif rel_path.endswith(".py"):
        rel_path = rel_path[:-3]
    return rel_path.replace("/", ".")


def parse_python_file(path: Path, root_dir: Path) -> ParsedFile:
    rel_path = to_repo_relative(path, root_dir)
    stat = path.stat()
    source = path.read_text(encoding="utf-8")
    parsed = ParsedFile(
        rel_path=rel_path,
        abs_path=str(path.resolve()),
        sha256=sha256_text(source),
        mtime=stat.st_mtime,
        size=stat.st_size,
    )

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        parsed.parse_status = "error"
        parsed.parse_error = str(exc)
        return parsed

    lines = source.splitlines()
    imports: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level
            module = f"{prefix}{node.module or ''}"
            for alias in node.names:
                if module:
                    separator = "" if module.endswith(".") else "."
                    imports.append(f"{module}{separator}{alias.name}")
                else:
                    imports.append(alias.name)
    parsed.imports = sorted(set(imports))

    file_is_test = _file_is_test(rel_path)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            parsed.symbols.append(
                _parse_symbol(node, rel_path, lines, "function", file_is_test=file_is_test)
            )
        elif isinstance(node, ast.ClassDef):
            class_symbol = _parse_symbol(node, rel_path, lines, "class", file_is_test=file_is_test)
            parsed.symbols.append(class_symbol)
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method_name = f"{node.name}.{child.name}"
                    parsed.symbols.append(
                        _parse_symbol(
                            child,
                            rel_path,
                            lines,
                            "method",
                            display_name=method_name,
                            parent_symbol_id=class_symbol.node_id,
                            file_is_test=file_is_test,
                        )
                    )
    return parsed


def _file_is_test(rel_path: str) -> bool:
    name = Path(rel_path).name
    return rel_path.startswith("tests/") or rel_path.startswith("test/") or name.startswith("test_") or name.endswith("_test.py")


def _parse_symbol(
    node: ast.AST,
    rel_path: str,
    lines: Sequence[str],
    kind: str,
    display_name: Optional[str] = None,
    parent_symbol_id: Optional[str] = None,
    file_is_test: bool = False,
) -> ParsedSymbol:
    name = display_name or getattr(node, "name")
    start_line = getattr(node, "lineno")
    end_line = getattr(node, "end_lineno", start_line)
    source = "\n".join(lines[start_line - 1 : end_line])
    signature = _signature_from_source(source)
    docstring = ast.get_docstring(node) or ""
    bases: List[str] = []
    if isinstance(node, ast.ClassDef):
        bases = sorted({_attr_or_name(base) for base in node.bases if _attr_or_name(base)})
    decorators: List[str] = []
    if hasattr(node, "decorator_list"):
        decorators = sorted({_attr_or_name(dec) for dec in node.decorator_list if _attr_or_name(dec)})
    is_test = (
        file_is_test
        and kind in {"function", "method"}
        and (name.startswith("test_") or (display_name or "").split(".")[-1].startswith("test_"))
    )
    return ParsedSymbol(
        node_id=symbol_node_id(rel_path, kind, name),
        kind=kind,
        name=name,
        file_path=rel_path,
        start_line=start_line,
        end_line=end_line,
        signature=signature,
        docstring=docstring,
        source=source,
        source_hash=sha256_text(source),
        parent_symbol_id=parent_symbol_id,
        calls=sorted(extract_called_symbols(source)),
        bases=bases,
        decorators=decorators,
        is_test=is_test,
    )


def _attr_or_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _attr_or_name(node.func)
    return ""


def _signature_from_source(source: str) -> str:
    first_line = source.splitlines()[0].strip() if source else ""
    if first_line.startswith(("def ", "async def ", "class ")):
        return first_line.rstrip(":").split("#")[0].strip()
    return ""


def extract_called_symbols(source: str) -> Set[str]:
    calls: Set[str] = set()
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return calls
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            calls.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            calls.add(node.func.attr)
    return calls


def resolve_local_import(
    import_name: str,
    module_to_file_id: Dict[str, str],
    current_module: str,
) -> Optional[str]:
    if import_name.startswith("."):
        dot_count = len(import_name) - len(import_name.lstrip("."))
        remainder = import_name[dot_count:]
        module_parts = current_module.split(".") if current_module else []
        if dot_count > len(module_parts):
            return None
        base_parts = module_parts[:-dot_count]
        remainder_parts = [part for part in remainder.split(".") if part]
        candidate = ".".join(base_parts + remainder_parts)
    else:
        candidate = import_name

    while candidate:
        if candidate in module_to_file_id:
            return module_to_file_id[candidate]
        if "." not in candidate:
            break
        candidate = candidate.rsplit(".", 1)[0]
    return None


def extract_query_entities(query_text: str, known_names: Iterable[str]) -> Set[str]:
    tokens = set(code_tokenize(query_text))
    known_lower = {name.lower(): name for name in known_names}
    entities: Set[str] = set()
    for token in tokens:
        if token in known_lower:
            entities.add(known_lower[token])
    for name in known_names:
        lowered = name.lower()
        if lowered in query_text.lower():
            entities.add(name)
    return entities
