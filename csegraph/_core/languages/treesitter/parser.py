from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Set

from tree_sitter import Node, Parser

from csegraph._core.core.ids import symbol_node_id
from csegraph._core.languages.base import BaseParser, sha256_text, to_repo_relative
from csegraph._core.languages.treesitter.config import LanguageConfig
from csegraph._core.languages.types import (
    ParsedFile,
    ParsedImport,
    ParsedReference,
    ParsedSymbol,
)

_IMPORT_NODE_TYPES = frozenset(
    {
        "import_statement",
        "import_from_statement",
        "import_declaration",
        "import_spec",
        "import_spec_list",
        "use_declaration",
        "preproc_include",
        "using_directive",
        "import_header",
    }
)


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


def _exact_source_of(node: Node, lines: List[str]) -> str:
    start_row, start_col = node.start_point
    end_row, end_col = node.end_point
    if start_row >= len(lines):
        return ""
    if start_row == end_row:
        return lines[start_row][start_col:end_col]
    selected = lines[start_row : min(end_row + 1, len(lines))]
    if not selected:
        return ""
    selected[0] = selected[0][start_col:]
    if end_row < len(lines):
        selected[-1] = selected[-1][:end_col]
    return "\n".join(selected)


def _bounded_source_of(
    node: Node, lines: List[str], *, max_lines: int = 3, max_chars: int = 240
) -> str:
    start = node.start_point[0]
    end = min(node.end_point[0], start + max_lines - 1)
    source = "\n".join(lines[start : end + 1])
    if len(source) > max_chars:
        return source[: max(0, max_chars - 3)].rstrip() + "..."
    return source


def _reference_from_node(
    *,
    kind: str,
    name: str,
    node: Node,
    lines: List[str],
    metadata: Optional[Dict[str, str]] = None,
) -> ParsedReference:
    return ParsedReference(
        kind=kind,
        name=name,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        source=_exact_source_of(node, lines),
        metadata=dict(metadata or {}),
    )


def _find_first_error(node: Node) -> Optional[Node]:
    if node.type == "ERROR":
        return node
    for child in node.children:
        found = _find_first_error(child)
        if found:
            return found
    return None


def _extract_import_records(
    root: Node,
    lines: List[str],
    import_names: List[str],
) -> List[ParsedImport]:
    if not import_names:
        return []
    candidates: List[Node] = []
    _collect_import_nodes(root, candidates)
    used: set[tuple[str, int, int]] = set()
    records: List[ParsedImport] = []
    for name in import_names:
        node = _match_import_node(name, candidates, lines)
        if node is not None:
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            source = _source_of(node, lines)
        else:
            start_line, end_line, source = _fallback_import_line(name, lines)
        key = (name, start_line, end_line)
        if key in used:
            continue
        used.add(key)
        records.append(
            ParsedImport(
                name=name,
                start_line=start_line,
                end_line=end_line,
                source=source,
                metadata=_import_metadata(name, source),
            )
        )
    return records


def _import_metadata(name: str, source: str) -> Dict[str, object]:
    metadata: Dict[str, object] = {
        "imported_name": name.rsplit(".", 1)[-1].rsplit("/", 1)[-1],
        "module": name,
    }
    stripped = " ".join(source.strip().split())
    if not stripped:
        return metadata
    metadata["type_only"] = bool(
        stripped.startswith("import type ") or " import type " in f" {stripped} "
    )
    require_match = re.match(
        r"(?:const|let|var)\s+([A-Za-z0-9_$]+)\s*=\s*require\(['\"]([^'\"]+)['\"]\)",
        stripped,
    )
    if require_match:
        local_name = require_match.group(1)
        module_name = require_match.group(2)
        metadata.update(
            {
                "style": "require",
                "module": module_name,
                "imported_name": module_name.rsplit("/", 1)[-1],
                "local_name": local_name,
                "imports": [{"name": module_name, "local": local_name}],
                "aliases": {local_name: module_name},
            }
        )
        return metadata
    from_match = re.match(r"from\s+([A-Za-z0-9_\\.]+)\s+import\s+(.+)", stripped)
    if from_match:
        module_name = from_match.group(1)
        metadata["style"] = "from"
        metadata["module"] = module_name
        imported = name.rsplit(".", 1)[-1]
        py_imports: List[Dict[str, str]] = []
        py_aliases: Dict[str, str] = {}
        for part in _split_import_parts(from_match.group(2)):
            item, alias = _split_alias(part, alias_words=("as",))
            local_name = alias or item.rsplit(".", 1)[-1]
            qualified = f"{module_name}.{item}" if module_name else item
            py_imports.append({"name": item, "local": local_name, "qualified": qualified})
            if local_name != qualified:
                py_aliases[local_name] = qualified
            if item == imported or item == name:
                metadata["imported_name"] = item
                metadata["local_name"] = local_name
        metadata["imports"] = py_imports
        if py_aliases:
            metadata["aliases"] = py_aliases
        return metadata
    import_match = re.match(r"import\s+(.+?)\s+from\s+['\"]([^'\"]+)['\"]", stripped)
    if import_match:
        metadata["style"] = "es"
        metadata["module"] = import_match.group(2)
        spec = import_match.group(1).removeprefix("type ").strip()
        es_imports: List[Dict[str, object]] = []
        es_aliases: Dict[str, str] = {}
        named_spec = spec
        if spec.startswith("* as "):
            local_name = spec.removeprefix("* as ").strip()
            metadata["namespace"] = local_name
            es_imports.append({"name": "*", "local": local_name})
            es_aliases[local_name] = "*"
            named_spec = ""
        elif not spec.startswith("{"):
            default_name = spec.split(",", 1)[0].strip()
            if default_name:
                metadata["default"] = default_name
                es_imports.append({"name": "default", "local": default_name})
            named_spec = spec.split(",", 1)[1].strip() if "," in spec else ""
        if "{" in named_spec and "}" in named_spec:
            named_spec = named_spec[named_spec.find("{") + 1 : named_spec.rfind("}")]
        for part in _split_import_parts(named_spec.strip("{}")):
            item = part.removeprefix("type ").strip()
            item, alias = _split_alias(item, alias_words=("as",))
            if not item:
                continue
            local_name = alias or item
            es_imports.append(
                {
                    "name": item,
                    "local": local_name,
                    "type_only": part.lstrip().startswith("type "),
                }
            )
            if local_name != item:
                es_aliases[local_name] = item
            if item == metadata["imported_name"] or item == name:
                metadata["imported_name"] = item
                metadata["local_name"] = local_name
        metadata["imports"] = es_imports
        if es_aliases:
            metadata["aliases"] = es_aliases
        return metadata
    simple_import_match = re.match(r"import\s+(.+)", stripped)
    if simple_import_match:
        simple_imports: List[Dict[str, str]] = []
        simple_aliases: Dict[str, str] = {}
        for part in _split_import_parts(simple_import_match.group(1)):
            item, alias = _split_alias(part, alias_words=("as",))
            local_name = alias or item.split(".", 1)[0]
            simple_imports.append({"name": item, "local": local_name})
            if local_name != item:
                simple_aliases[local_name] = item
            if item == name:
                metadata["imported_name"] = item.rsplit(".", 1)[-1]
                metadata["local_name"] = local_name
                metadata["module"] = item
        metadata["style"] = "import"
        metadata["imports"] = simple_imports
        if simple_aliases:
            metadata["aliases"] = simple_aliases
        return metadata
    alias_match = re.match(r"import\s+([A-Za-z0-9_\\.]+)\s+as\s+([A-Za-z0-9_]+)", stripped)
    if alias_match:
        metadata["imported_name"] = alias_match.group(1).rsplit(".", 1)[-1]
        metadata["local_name"] = alias_match.group(2)
        metadata["module"] = alias_match.group(1)
    return metadata


def _split_import_parts(raw: str) -> List[str]:
    cleaned = raw.replace("(", "").replace(")", "")
    return [part.strip() for part in cleaned.split(",") if part.strip()]


def _split_alias(part: str, *, alias_words: tuple[str, ...]) -> tuple[str, str]:
    for word in alias_words:
        marker = f" {word} "
        if marker in part:
            left, right = part.split(marker, 1)
            return left.strip(), right.strip()
    if ":" in part:
        left, right = part.split(":", 1)
        return left.strip(), right.strip()
    return part.strip(), ""


def _collect_import_nodes(node: Node, result: List[Node]) -> None:
    if node.type in _IMPORT_NODE_TYPES:
        result.append(node)
    for child in node.children:
        _collect_import_nodes(child, result)


def _match_import_node(name: str, candidates: List[Node], lines: List[str]) -> Optional[Node]:
    needles = {
        name,
        name.rsplit(".", 1)[-1],
        name.rsplit("/", 1)[-1],
        name.strip("\"'`"),
    }
    needles = {needle for needle in needles if needle}
    for node in candidates:
        source = _source_of(node, lines)
        if any(needle in source for needle in needles):
            return node
    return candidates[0] if len(candidates) == 1 else None


def _fallback_import_line(name: str, lines: List[str]) -> tuple[int, int, str]:
    needles = (
        name,
        name.rsplit(".", 1)[-1],
        name.rsplit("/", 1)[-1],
    )
    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if any(needle and needle in stripped for needle in needles) and (
            "import" in stripped
            or "require" in stripped
            or stripped.startswith("use ")
            or stripped.startswith("#include")
            or stripped.startswith("using ")
        ):
            return index, index, line
    return 1, 1, ""


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
            parsed.import_records = _extract_import_records(
                tree.root_node,
                lines,
                parsed.imports,
            )
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
                if child.type in {"block", "suite"}:
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
                    continue
                targets: list[Node] = []
                for field in ("body", "consequence", "alternative", "handler"):
                    target = child.child_by_field_name(field)
                    if target is not None:
                        targets.append(target)
                targets.extend(
                    nested
                    for nested in child.children
                    if nested.type
                    in {
                        "block",
                        "suite",
                        "except_clause",
                        "else_clause",
                        "finally_clause",
                    }
                    and nested not in targets
                )
                for target in targets:
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
        base_refs = self._extract_base_references(node, lines)
        call_refs = self._extract_call_references(node, lines)
        decorator_refs = self._extract_decorator_references(effective_node, node, lines)
        bases = sorted({ref.name for ref in base_refs})
        calls = sorted({ref.name for ref in call_refs})
        decorators = sorted({ref.name for ref in decorator_refs})
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
            references=call_refs + base_refs + decorator_refs,
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

        call_refs = self._extract_call_references(node, lines)
        decorator_refs = self._extract_decorator_references(effective_node, node, lines)
        calls = sorted({ref.name for ref in call_refs})
        decorators = sorted({ref.name for ref in decorator_refs})
        is_test = (
            file_is_test
            and kind in {"function", "method"}
            and any(name.startswith(p) for p in cfg.test_name_prefixes)
        )
        test_refs = [
            ParsedReference(
                kind="test",
                name=ref.name,
                start_line=ref.start_line,
                end_line=ref.end_line,
                source=ref.source,
                metadata={**ref.metadata, "via": "call"},
            )
            for ref in call_refs
            if is_test
        ]

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
                references=call_refs + decorator_refs + test_refs,
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
            call_refs = self._extract_call_references(value, lines)
            calls = sorted({ref.name for ref in call_refs})
            is_test = file_is_test and any(name.startswith(p) for p in cfg.test_name_prefixes)
            test_refs = [
                ParsedReference(
                    kind="test",
                    name=ref.name,
                    start_line=ref.start_line,
                    end_line=ref.end_line,
                    source=ref.source,
                    metadata={**ref.metadata, "via": "call"},
                )
                for ref in call_refs
                if is_test
            ]

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
                    references=call_refs + test_refs,
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

    def _extract_base_references(self, node: Node, lines: List[str]) -> List[ParsedReference]:
        cfg = self._config
        refs: List[ParsedReference] = []
        seen: set[tuple[str, int, int]] = set()

        def collect(candidate: Optional[Node]) -> None:
            if candidate is None:
                return
            if candidate.type in cfg.heritage_ident_types:
                name = _node_text(candidate)
                key = (name, candidate.start_point[0], candidate.end_point[0])
                if name and key not in seen:
                    seen.add(key)
                    refs.append(
                        _reference_from_node(
                            kind="inherits",
                            name=name,
                            node=candidate,
                            lines=lines,
                        )
                    )
                return
            for child in candidate.children:
                collect(child)

        if cfg.superclass_field:
            collect(node.child_by_field_name(cfg.superclass_field))
        if cfg.interfaces_field:
            collect(node.child_by_field_name(cfg.interfaces_field))
        if cfg.heritage_type:
            for child in node.children:
                if child.type == cfg.heritage_type:
                    collect(child)
        return refs

    def _collect_ident_names(self, node: Node, names: Set[str]) -> None:
        cfg = self._config
        if node.type in cfg.heritage_ident_types:
            names.add(_node_text(node))
        for child in node.children:
            self._collect_ident_names(child, names)

    def _extract_calls(self, node: Node) -> List[str]:
        return sorted({ref.name for ref in self._extract_call_references(node, [])})

    def _extract_call_references(self, node: Node, lines: List[str]) -> List[ParsedReference]:
        refs: List[ParsedReference] = []
        body = node.child_by_field_name("body")
        self._walk_call_references(body if body else node, lines, refs)
        return refs

    def _walk_calls(self, node: Node, calls: Set[str]) -> None:
        for ref in self._extract_call_references(node, []):
            calls.add(ref.name)

    def _walk_call_references(
        self,
        node: Optional[Node],
        lines: List[str],
        refs: List[ParsedReference],
    ) -> None:
        if node is None:
            return
        cfg = self._config
        if node.type in cfg.call_types:
            name_node = self._call_name_node(node)
            if name_node:
                name = _node_text(name_node)
                if name:
                    refs.append(
                        _reference_from_node(
                            kind="calls",
                            name=name,
                            node=node,
                            lines=lines,
                            metadata={"name_node_type": name_node.type},
                        )
                    )
            else:
                type_node = node.child_by_field_name("type")
                if type_node:
                    name = _node_text(type_node)
                    if name:
                        refs.append(
                            _reference_from_node(
                                kind="calls",
                                name=name,
                                node=node,
                                lines=lines,
                                metadata={"name_node_type": type_node.type},
                            )
                        )
        for child in node.children:
            if child.type not in cfg.function_boundary_types:
                self._walk_call_references(child, lines, refs)

    def _call_name_node(self, node: Node) -> Optional[Node]:
        fn = node.child_by_field_name("function")
        if not fn:
            fn = node.child_by_field_name("name")
        if not fn:
            return None
        if fn.type == "identifier":
            return fn
        if fn.type == "member_expression":
            return fn.child_by_field_name("property")
        if fn.type == "field_access":
            return fn.child_by_field_name("field")
        if fn.type == "field_expression":
            return fn.child_by_field_name("field")
        if fn.type == "scoped_identifier":
            return fn.child_by_field_name("name")
        if fn.type == "selector_expression":
            return fn.child_by_field_name("field")
        if fn.type == "attribute":
            return fn.child_by_field_name("attribute")
        return None

    def _extract_doc(self, node: Node, lines: List[str]) -> str:
        if self._config.extract_doc_fn:
            return self._config.extract_doc_fn(node, lines)
        return _extract_jsdoc(node, lines)

    def _extract_decorators(self, effective_node: Node, node: Node) -> List[str]:
        """Extract decorator names when the symbol is wrapped in a decorator_wrapper_type."""
        return [ref.name for ref in self._extract_decorator_references(effective_node, node, [])]

    def _extract_decorator_references(
        self,
        effective_node: Node,
        node: Node,
        lines: List[str],
    ) -> List[ParsedReference]:
        """Extract decorator occurrence records when the symbol is wrapped."""
        decorators: List[ParsedReference] = []
        for child in effective_node.children:
            if child.type == "decorator":
                text = _node_text(child).lstrip("@").split("(")[0].strip()
                if text:
                    decorators.append(
                        _reference_from_node(
                            kind="decorates",
                            name=text,
                            node=child,
                            lines=lines,
                        )
                    )
        return sorted(decorators, key=lambda ref: (ref.start_line, ref.name))

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
