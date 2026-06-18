from __future__ import annotations

from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Set

from tree_sitter import Node, Parser

from csegraph._core.core.ids import symbol_node_id
from csegraph._core.languages.base import BaseParser, sha256_text, to_repo_relative
from csegraph._core.languages.treesitter.config import LanguageConfig
from csegraph._core.languages.types import ParsedFile, ParsedSymbol


def _node_text(node: Node) -> str:
    return node.text.decode("utf-8") if node.text else ""


def _name_of(node: Node, field: str = "name") -> str:
    name_node = node.child_by_field_name(field)
    if name_node:
        return _node_text(name_node)
    decl = node.child_by_field_name("declarator")
    if decl:
        inner = decl.child_by_field_name("declarator")
        if inner:
            return _node_text(inner)
        return _node_text(decl)
    return ""


def _signature_from_node(node: Node, lines: List[str]) -> str:
    start = node.start_point[0]
    if start < len(lines):
        first = lines[start].strip()
        if first.endswith("{"):
            first = first[:-1].strip()
        return first
    return ""


def _source_of(node: Node, lines: List[str]) -> str:
    start = node.start_point[0]
    end = node.end_point[0]
    return "\n".join(lines[start : end + 1])


def _find_first_error(node: Node) -> Optional[Node]:
    if node.type == "ERROR":
        return node
    for child in node.children:
        found = _find_first_error(child)
        if found:
            return found
    return None


def _extract_jsdoc(node: Node, lines: List[str]) -> str:
    start = node.start_point[0]
    if start == 0:
        return ""
    prev_line = lines[start - 1].strip() if start - 1 < len(lines) else ""
    if prev_line.endswith("*/"):
        doc_lines: List[str] = []
        i = start - 1
        while i >= 0:
            line = lines[i].strip()
            doc_lines.insert(0, line)
            if line.startswith("/**") or line.startswith("/*"):
                break
            i -= 1
        text = "\n".join(doc_lines)
        text = text.lstrip("/*").rstrip("*/").strip()
        cleaned: List[str] = []
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("* "):
                line = line[2:]
            elif line == "*":
                line = ""
            cleaned.append(line)
        return "\n".join(cleaned).strip()
    return ""


def _extract_line_doc(node: Node, lines: List[str], prefix: str = "///") -> str:
    start = node.start_point[0]
    if start == 0:
        return ""
    doc_lines: List[str] = []
    i = start - 1
    while i >= 0:
        line = lines[i].strip()
        if line.startswith(prefix):
            doc_lines.insert(0, line[len(prefix) :].strip())
            i -= 1
        else:
            break
    return "\n".join(doc_lines).strip()


def _extract_go_doc(node: Node, lines: List[str]) -> str:
    start = node.start_point[0]
    if start == 0:
        return ""
    doc_lines: List[str] = []
    i = start - 1
    while i >= 0:
        line = lines[i].strip()
        if line.startswith("//"):
            doc_lines.insert(0, line[2:].strip())
            i -= 1
        else:
            break
    return "\n".join(doc_lines).strip()


class TreeSitterParser(BaseParser):
    def __init__(self, config: LanguageConfig) -> None:
        self._config = config
        self.language = config.name
        self.extensions = config.extensions

    @property
    def config(self) -> LanguageConfig:
        return self._config

    @property
    def extra_excluded_dirs(self) -> FrozenSet[str]:
        return self._config.extra_excluded_dirs

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
            language=self._config.name,
        )

        suffix = path.suffix
        lang = self._config.lang_map.get(suffix)
        if lang is None:
            lang = next(iter(self._config.lang_map.values()))
        parser = Parser(lang)
        tree = parser.parse(source.encode("utf-8"))
        if tree.root_node.has_error:
            error_node = _find_first_error(tree.root_node)
            if error_node and error_node.type == "ERROR" and tree.root_node.child_count <= 1:
                parsed.parse_status = "error"
                line = error_node.start_point[0] + 1
                parsed.parse_error = f"Syntax error at line {line}"
                return parsed

        lines = source.splitlines()
        file_is_test = self._file_is_test(rel_path)

        if self._config.extract_imports_fn:
            parsed.imports = self._config.extract_imports_fn(tree.root_node)
        self._extract_symbols(
            tree.root_node,
            rel_path,
            lines,
            parsed.symbols,
            file_is_test,
        )
        return parsed

    def module_name_from_relpath(self, rel_path: str) -> Optional[str]:
        if self._config.module_name_fn:
            return self._config.module_name_fn(rel_path)
        for ext in self._config.extensions:
            if rel_path.endswith(ext):
                return rel_path[: -len(ext)].replace("/", ".")
        return rel_path.replace("/", ".")

    def resolve_local_import(
        self,
        import_name: str,
        module_to_file_id: Dict[str, str],
        current_module: Optional[str],
    ) -> Optional[str]:
        if self._config.resolve_import_fn:
            return self._config.resolve_import_fn(
                import_name,
                module_to_file_id,
                current_module,
            )
        return None

    def _file_is_test(self, rel_path: str) -> bool:
        name = Path(rel_path).stem
        for prefix in self._config.test_dir_prefixes:
            if rel_path.startswith(prefix):
                return True
        for pattern in ("/__tests__/", "/__test__/"):
            if pattern in rel_path:
                return True
        for suffix in self._config.test_file_suffixes:
            if name.endswith(suffix):
                return True
        if name.startswith("test_"):
            return True
        return False

    def _extract_symbols(
        self,
        root: Node,
        rel_path: str,
        lines: List[str],
        symbols: List[ParsedSymbol],
        file_is_test: bool,
        parent_class_id: Optional[str] = None,
        class_name: Optional[str] = None,
        class_map: Optional[Dict[str, str]] = None,
    ) -> None:
        cfg = self._config
        top_level = class_map is None
        if class_map is None:
            class_map = {}

        for child in root.children:
            if child.type in cfg.class_types:
                self._extract_class(
                    child,
                    rel_path,
                    lines,
                    symbols,
                    file_is_test,
                    class_map,
                )
            elif child.type in cfg.function_types:
                self._extract_function(
                    child,
                    rel_path,
                    lines,
                    symbols,
                    file_is_test,
                    parent_class_id,
                    class_name,
                    class_map,
                )
            elif cfg.export_type and child.type == cfg.export_type:
                self._extract_symbols(
                    child,
                    rel_path,
                    lines,
                    symbols,
                    file_is_test,
                    parent_class_id,
                    class_name,
                    class_map,
                )
            elif child.type in cfg.declaration_wrapper_types:
                body = child.child_by_field_name("body")
                target = body if body else child
                self._extract_symbols(
                    target,
                    rel_path,
                    lines,
                    symbols,
                    file_is_test,
                    parent_class_id,
                    class_name,
                    class_map,
                )
            elif child.type in cfg.lambda_decl_types:
                self._extract_arrow_functions(
                    child,
                    rel_path,
                    lines,
                    symbols,
                    file_is_test,
                )
            elif child.type in cfg.impl_types:
                self._extract_impl_block(
                    child,
                    rel_path,
                    lines,
                    symbols,
                    file_is_test,
                    class_map,
                )
            elif cfg.decorator_wrapper_type and child.type == cfg.decorator_wrapper_type:
                self._extract_symbols(
                    child,
                    rel_path,
                    lines,
                    symbols,
                    file_is_test,
                    parent_class_id,
                    class_name,
                    class_map,
                )

        if top_level:
            self._fixup_methods(symbols, class_map)

    def _extract_class(
        self,
        node: Node,
        rel_path: str,
        lines: List[str],
        symbols: List[ParsedSymbol],
        file_is_test: bool,
        class_map: Dict[str, str],
    ) -> None:
        cfg = self._config
        name = _name_of(node, cfg.class_name_field)
        if not name:
            return
        effective_node = node
        if (
            cfg.decorator_wrapper_type
            and node.parent
            and node.parent.type == cfg.decorator_wrapper_type
        ):
            effective_node = node.parent
        start_line = effective_node.start_point[0] + 1
        end_line = effective_node.end_point[0] + 1
        source = _source_of(effective_node, lines)
        bases = self._extract_bases(node)
        calls = self._extract_calls(node)
        decorators = self._extract_decorators(effective_node, node)
        kind = "class"

        class_symbol = ParsedSymbol(
            node_id=symbol_node_id(rel_path, kind, name),
            kind=kind,
            name=name,
            file_path=rel_path,
            start_line=start_line,
            end_line=end_line,
            signature=_signature_from_node(node, lines),
            docstring=self._extract_doc(node, lines),
            source=source,
            source_hash=sha256_text(source),
            calls=calls,
            bases=bases,
            decorators=decorators,
        )
        symbols.append(class_symbol)
        class_map[name] = class_symbol.node_id

        body = node.child_by_field_name(cfg.class_body_field)
        if not body and cfg.class_body_type:
            for child in node.children:
                if child.type == cfg.class_body_type:
                    body = child
                    break
        if body:
            for child in body.children:
                if child.type in cfg.function_types:
                    self._extract_function(
                        child,
                        rel_path,
                        lines,
                        symbols,
                        file_is_test,
                        parent_class_id=class_symbol.node_id,
                        class_name=name,
                    )
                elif cfg.decorator_wrapper_type and child.type == cfg.decorator_wrapper_type:
                    for inner in child.children:
                        if inner.type in cfg.function_types:
                            self._extract_function(
                                inner,
                                rel_path,
                                lines,
                                symbols,
                                file_is_test,
                                parent_class_id=class_symbol.node_id,
                                class_name=name,
                            )

    def _extract_function(
        self,
        node: Node,
        rel_path: str,
        lines: List[str],
        symbols: List[ParsedSymbol],
        file_is_test: bool,
        parent_class_id: Optional[str] = None,
        class_name: Optional[str] = None,
        class_map: Optional[Dict[str, str]] = None,
    ) -> None:
        cfg = self._config
        name = _name_of(node, cfg.class_name_field)
        if not name:
            return
        effective_node = node
        if (
            cfg.decorator_wrapper_type
            and node.parent
            and node.parent.type == cfg.decorator_wrapper_type
        ):
            effective_node = node.parent
        start_line = effective_node.start_point[0] + 1
        end_line = effective_node.end_point[0] + 1
        source = _source_of(effective_node, lines)

        if cfg.method_receiver_field and not parent_class_id:
            receiver = node.child_by_field_name(cfg.method_receiver_field)
            if receiver:
                recv_type = self._receiver_type_name(receiver)
                if recv_type:
                    parent_class_id = (class_map or {}).get(recv_type)
                    class_name = recv_type

        if parent_class_id or class_name:
            kind = "method"
            display_name = f"{class_name}.{name}" if class_name else name
        else:
            kind = "function"
            display_name = name

        calls = self._extract_calls(node)
        decorators = self._extract_decorators(effective_node, node)
        is_test = (
            file_is_test
            and kind in {"function", "method"}
            and any(name.startswith(p) for p in cfg.test_name_prefixes)
        )

        symbols.append(
            ParsedSymbol(
                node_id=symbol_node_id(rel_path, kind, display_name),
                kind=kind,
                name=display_name,
                file_path=rel_path,
                start_line=start_line,
                end_line=end_line,
                signature=_signature_from_node(node, lines),
                docstring=self._extract_doc(node, lines),
                source=source,
                source_hash=sha256_text(source),
                parent_symbol_id=parent_class_id,
                calls=calls,
                decorators=decorators,
                is_test=is_test,
            )
        )

    def _extract_arrow_functions(
        self,
        node: Node,
        rel_path: str,
        lines: List[str],
        symbols: List[ParsedSymbol],
        file_is_test: bool,
    ) -> None:
        cfg = self._config
        for child in node.children:
            if child.type != "variable_declarator":
                continue
            name = _name_of(child)
            if not name:
                continue
            value = child.child_by_field_name("value")
            if not value or value.type != cfg.lambda_value_type:
                continue
            start_line = child.start_point[0] + 1
            end_line = child.end_point[0] + 1
            source = _source_of(child, lines)
            calls = self._extract_calls(value)
            is_test = file_is_test and any(name.startswith(p) for p in cfg.test_name_prefixes)

            symbols.append(
                ParsedSymbol(
                    node_id=symbol_node_id(rel_path, "function", name),
                    kind="function",
                    name=name,
                    file_path=rel_path,
                    start_line=start_line,
                    end_line=end_line,
                    signature=_signature_from_node(child, lines),
                    docstring="",
                    source=source,
                    source_hash=sha256_text(source),
                    calls=calls,
                    is_test=is_test,
                )
            )

    def _extract_impl_block(
        self,
        node: Node,
        rel_path: str,
        lines: List[str],
        symbols: List[ParsedSymbol],
        file_is_test: bool,
        class_map: Dict[str, str],
    ) -> None:
        cfg = self._config
        type_node = node.child_by_field_name(cfg.impl_type_field)
        impl_name = _node_text(type_node) if type_node else ""
        if not impl_name:
            return
        parent_id = class_map.get(impl_name)
        body = node.child_by_field_name(cfg.impl_body_field)
        if body:
            for child in body.children:
                if child.type in cfg.function_types:
                    self._extract_function(
                        child,
                        rel_path,
                        lines,
                        symbols,
                        file_is_test,
                        parent_class_id=parent_id,
                        class_name=impl_name,
                    )

    def _extract_bases(self, node: Node) -> List[str]:
        cfg = self._config
        bases: Set[str] = set()

        if cfg.superclass_field:
            sc = node.child_by_field_name(cfg.superclass_field)
            if sc:
                if sc.type in cfg.heritage_ident_types:
                    bases.add(_node_text(sc))
                else:
                    for child in sc.children:
                        if child.type in cfg.heritage_ident_types:
                            bases.add(_node_text(child))

        if cfg.interfaces_field:
            ifaces = node.child_by_field_name(cfg.interfaces_field)
            if ifaces:
                self._collect_ident_names(ifaces, bases)

        if cfg.heritage_type:
            for child in node.children:
                if child.type == cfg.heritage_type:
                    for clause in child.children:
                        if clause.type in cfg.heritage_clause_types:
                            for ident in clause.children:
                                if ident.type in cfg.heritage_ident_types:
                                    bases.add(_node_text(ident))

        return sorted(bases)

    def _collect_ident_names(self, node: Node, names: Set[str]) -> None:
        cfg = self._config
        if node.type in cfg.heritage_ident_types:
            names.add(_node_text(node))
        for child in node.children:
            self._collect_ident_names(child, names)

    def _extract_calls(self, node: Node) -> List[str]:
        calls: Set[str] = set()
        body = node.child_by_field_name("body")
        if body:
            self._walk_calls(body, calls)
        return sorted(calls)

    def _walk_calls(self, node: Node, calls: Set[str]) -> None:
        cfg = self._config
        if node.type in cfg.call_types:
            fn = node.child_by_field_name("function")
            if not fn:
                fn = node.child_by_field_name("name")
            if fn:
                if fn.type == "identifier":
                    calls.add(_node_text(fn))
                elif fn.type == "member_expression":
                    prop = fn.child_by_field_name("property")
                    if prop:
                        calls.add(_node_text(prop))
                elif fn.type == "field_access":
                    field = fn.child_by_field_name("field")
                    if field:
                        calls.add(_node_text(field))
                elif fn.type == "field_expression":
                    field = fn.child_by_field_name("field")
                    if field:
                        calls.add(_node_text(field))
                elif fn.type == "scoped_identifier":
                    name_node = fn.child_by_field_name("name")
                    if name_node:
                        calls.add(_node_text(name_node))
                elif fn.type == "selector_expression":
                    field = fn.child_by_field_name("field")
                    if field:
                        calls.add(_node_text(field))
                elif fn.type == "attribute":
                    attr = fn.child_by_field_name("attribute")
                    if attr:
                        calls.add(_node_text(attr))
            else:
                type_node = node.child_by_field_name("type")
                if type_node:
                    calls.add(_node_text(type_node))
        for child in node.children:
            if child.type not in cfg.function_boundary_types:
                self._walk_calls(child, calls)

    def _extract_doc(self, node: Node, lines: List[str]) -> str:
        if self._config.extract_doc_fn:
            return self._config.extract_doc_fn(node, lines)
        return _extract_jsdoc(node, lines)

    def _extract_decorators(self, effective_node: Node, node: Node) -> List[str]:
        """Extract decorator names when the symbol is wrapped in a decorator_wrapper_type."""
        if effective_node is node:
            return []
        decorators: List[str] = []
        for child in effective_node.children:
            if child.type == "decorator":
                text = _node_text(child).lstrip("@").split("(")[0].strip()
                if text:
                    decorators.append(text)
        return sorted(decorators)

    def _receiver_type_name(self, receiver: Node) -> str:
        for child in receiver.children:
            if child.type == "parameter_declaration":
                type_node = child.child_by_field_name("type")
                if type_node:
                    if type_node.type == "pointer_type":
                        for inner in type_node.children:
                            if inner.type == "type_identifier":
                                return _node_text(inner)
                    elif type_node.type == "type_identifier":
                        return _node_text(type_node)
        return ""

    def _fixup_methods(
        self,
        symbols: List[ParsedSymbol],
        class_map: Dict[str, str],
    ) -> None:
        for sym in symbols:
            if sym.kind == "method" and not sym.parent_symbol_id:
                class_name = sym.name.split(".")[0] if "." in sym.name else None
                if class_name and class_name in class_map:
                    sym.parent_symbol_id = class_map[class_name]
