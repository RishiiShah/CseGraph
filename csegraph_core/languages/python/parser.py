from __future__ import annotations

import ast
import hashlib
import os
import textwrap
from collections import defaultdict
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Sequence, Set

from csegraph_core.core.ids import symbol_node_id
from csegraph_core.languages.types import ParsedFile, ParsedSymbol

__all__ = [
    "EXCLUDED_DIRS",
    "ParsedSymbol",
    "ParsedFile",
    "PythonParser",
    "sha256_text",
    "sha256_file",
    "to_repo_relative",
    "extract_called_symbols",
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


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def to_repo_relative(path: Path, root_dir: Path) -> str:
    return path.resolve().relative_to(root_dir.resolve()).as_posix()


def extract_called_symbols(source: str) -> Set[str]:
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return set()
    return _extract_called_symbols_from_ast(tree)


class PythonParser:
    language = "python"
    extensions = (".py",)

    @property
    def excluded_dirs(self) -> FrozenSet[str]:
        return frozenset(EXCLUDED_DIRS)

    def iter_files(self, root_dir: Path) -> List[Path]:
        from csegraph_core.ignore import load_ignore_filter

        ignore = load_ignore_filter(root_dir)
        resolved_root = root_dir.resolve()
        paths: List[Path] = []
        for root, dirs, files in os.walk(root_dir):
            rel_root = Path(root).resolve().relative_to(resolved_root).as_posix()
            dirs[:] = sorted(
                name for name in dirs
                if name not in EXCLUDED_DIRS
                and not name.startswith(".")
                and not ignore.is_ignored(
                    f"{rel_root}/{name}" if rel_root != "." else name,
                    is_dir=True,
                )
            )
            for filename in sorted(files):
                if filename.startswith(".") or not filename.endswith(".py"):
                    continue
                rel_path = f"{rel_root}/{filename}" if rel_root != "." else filename
                if not ignore.is_ignored(rel_path):
                    paths.append(Path(root) / filename)
        return sorted(paths)

    def parse(self, path: Path, root_dir: Path) -> ParsedFile:
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
        visitor = _FileVisitor(_emitted_scope_ids(tree))
        visitor.visit(tree)
        parsed.imports = sorted(set(visitor.imports))
        calls_by_node = visitor.calls_by_node

        file_is_test = _file_is_test(rel_path)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                parsed.symbols.append(
                    _parse_symbol(
                        node,
                        rel_path,
                        lines,
                        "function",
                        file_is_test=file_is_test,
                        calls=calls_by_node.get(id(node), set()),
                    )
                )
            elif isinstance(node, ast.ClassDef):
                class_symbol = _parse_symbol(
                    node,
                    rel_path,
                    lines,
                    "class",
                    file_is_test=file_is_test,
                    calls=calls_by_node.get(id(node), set()),
                )
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
                                calls=calls_by_node.get(id(child), set()),
                            )
                        )
        return parsed

    def module_name_from_relpath(self, rel_path: str) -> str:
        if rel_path.endswith("/__init__.py"):
            rel_path = rel_path[: -len("/__init__.py")]
        elif rel_path.endswith(".py"):
            rel_path = rel_path[:-3]
        return rel_path.replace("/", ".")

    def resolve_local_import(
        self,
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


def _file_is_test(rel_path: str) -> bool:
    name = Path(rel_path).name
    return (
        rel_path.startswith("tests/")
        or rel_path.startswith("test/")
        or name.startswith("test_")
        or name.endswith("_test.py")
    )


def _parse_symbol(
    node: ast.AST,
    rel_path: str,
    lines: Sequence[str],
    kind: str,
    display_name: Optional[str] = None,
    parent_symbol_id: Optional[str] = None,
    file_is_test: bool = False,
    calls: Optional[Set[str]] = None,
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
        calls=sorted(calls if calls is not None else _extract_called_symbols_from_ast(node)),
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


def _extract_called_symbols_from_ast(node: ast.AST) -> Set[str]:
    calls: Set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        if isinstance(child.func, ast.Name):
            calls.add(child.func.id)
        elif isinstance(child.func, ast.Attribute):
            calls.add(child.func.attr)
    return calls


def _emitted_scope_ids(tree: ast.Module) -> Set[int]:
    ids: Set[int] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            ids.add(id(node))
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    ids.add(id(child))
    return ids


class _FileVisitor(ast.NodeVisitor):
    """Single-pass visitor: collects file-level imports and per-scope calls together."""

    def __init__(self, emitted_scope_ids: Set[int]) -> None:
        self._emitted_scope_ids = emitted_scope_ids
        self._scope_stack: List[ast.AST] = []
        self.calls_by_node: Dict[int, Set[str]] = defaultdict(set)
        self.imports: List[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        self.imports.extend(alias.name for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        prefix = "." * node.level
        module = f"{prefix}{node.module or ''}"
        for alias in node.names:
            if module:
                separator = "" if module.endswith(".") else "."
                self.imports.append(f"{module}{separator}{alias.name}")
            else:
                self.imports.append(alias.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_scope(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_scope(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_scope(node)

    def visit_Call(self, node: ast.Call) -> None:
        if self._scope_stack:
            name = _attr_or_name(node.func)
            if name:
                self.calls_by_node[id(self._scope_stack[-1])].add(name)
        self.generic_visit(node)

    def _visit_scope(self, node: ast.AST) -> None:
        if id(node) not in self._emitted_scope_ids:
            self.generic_visit(node)
            return
        self._scope_stack.append(node)
        self.generic_visit(node)
        self._scope_stack.pop()


def _signature_from_source(source: str) -> str:
    first_line = source.splitlines()[0].strip() if source else ""
    if first_line.startswith(("def ", "async def ", "class ")):
        return first_line.rstrip(":").split("#")[0].strip()
    return ""
