"""Tree-sitter configuration for Python, JavaScript, and TypeScript."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Dict, List, Optional, Set

from tree_sitter import Language, Node

from csegraph._core.languages.treesitter.config import LanguageConfig


@dataclass(frozen=True)
class LanguageLoader:
    module: str
    function: str = "language"


class LazyLanguageMap(dict):
    """Load required tree-sitter grammars only when parsing begins."""

    def __init__(self, loaders: Dict[str, LanguageLoader]) -> None:
        self._loaders = loaders
        self._cache: Dict[str, Language] = {}

    def get(self, key, default=None):
        if key in self._cache:
            return self._cache[key]
        loader = self._loaders.get(key)
        if loader is None:
            return default
        module = import_module(loader.module)
        language = Language(getattr(module, loader.function)())
        self._cache[key] = language
        return language

    def values(self):
        if not self._cache and self._loaders:
            self.get(next(iter(self._loaders)))
        return self._cache.values()

    def __getitem__(self, key):
        value = self.get(key)
        if value is None:
            raise KeyError(key)
        return value

    def __contains__(self, key):
        return key in self._loaders

    def __iter__(self):
        return iter(self._loaders)

    def __len__(self):
        return len(self._loaders)


def _node_text(node: Node) -> str:
    return node.text.decode("utf-8") if node.text else ""


def _string_value(node: Node) -> str:
    for child in node.children:
        if child.type == "string_fragment":
            return _node_text(child)
    text = _node_text(node)
    if len(text) >= 2 and text[0] in ('"', "'", "`") and text[-1] == text[0]:
        return text[1:-1]
    return ""


def _typescript_extract_imports(root: Node) -> List[str]:
    imports: Set[str] = set()
    for child in root.children:
        if child.type == "import_statement":
            _typescript_collect_import(child, imports)
        elif child.type == "export_statement":
            for sub in child.children:
                if sub.type == "import_statement":
                    _typescript_collect_import(sub, imports)
        elif child.type in ("lexical_declaration", "variable_declaration"):
            _typescript_collect_require(child, imports)
    return sorted(imports)


def _typescript_collect_import(node: Node, imports: Set[str]) -> None:
    for child in node.children:
        if child.type == "string":
            path = _string_value(child)
            if path:
                imports.add(path)


def _typescript_collect_require(node: Node, imports: Set[str]) -> None:
    for child in node.children:
        if child.type != "variable_declarator":
            continue
        value = child.child_by_field_name("value")
        if not value:
            continue
        call = value if value.type == "call_expression" else None
        if not call and value.type == "member_expression":
            obj = value.child_by_field_name("object")
            if obj and obj.type == "call_expression":
                call = obj
        if not call:
            continue
        function = call.child_by_field_name("function")
        if not function or _node_text(function) != "require":
            continue
        arguments = call.child_by_field_name("arguments")
        if not arguments:
            continue
        for argument in arguments.children:
            if argument.type == "string":
                path = _string_value(argument)
                if path:
                    imports.add(path)


def _typescript_module_name(rel_path: str) -> Optional[str]:
    index_filenames = ("index.ts", "index.tsx", "index.js", "index.jsx")
    for index_filename in index_filenames:
        if rel_path.endswith(f"/{index_filename}") or rel_path == index_filename:
            return (
                rel_path.rsplit(f"/{index_filename.split('.')[0]}.", 1)[0].replace("/", ".")
                if "/" in rel_path
                else rel_path.rsplit(".", 1)[0].replace("/", ".")
            )
    for extension in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
        if rel_path.endswith(extension):
            return rel_path[: -len(extension)].replace("/", ".")
    return rel_path.replace("/", ".")


def _typescript_resolve_import(
    import_name: str,
    module_to_file_id: Dict[str, str],
    current_module: Optional[str],
) -> Optional[str]:
    if not import_name.startswith(".") or current_module is None:
        return None
    current_parts = current_module.split(".")
    # Dots in test filenames are not directory separators:
    # ``tests/service.test.ts`` maps to ``tests.service.test``.
    filename_parts = 2 if current_parts[-1] in {"test", "spec"} else 1
    base_parts = current_parts[:-filename_parts]
    for segment in import_name.split("/"):
        if segment == ".":
            continue
        if segment == "..":
            if base_parts:
                base_parts.pop()
        else:
            base_parts.append(segment)
    candidate = ".".join(base_parts)
    if candidate in module_to_file_id:
        return module_to_file_id[candidate]
    index_candidate = f"{candidate}.index"
    if index_candidate in module_to_file_id:
        return module_to_file_id[index_candidate]
    return None


def _python_extract_imports(root: Node) -> List[str]:
    imports: List[str] = []
    for child in root.children:
        if child.type == "import_statement":
            for grandchild in child.named_children:
                if grandchild.type == "dotted_name":
                    imports.append(_node_text(grandchild))
                elif grandchild.type == "aliased_import":
                    name = grandchild.child_by_field_name("name")
                    if name:
                        imports.append(_node_text(name))
        elif child.type == "import_from_statement":
            module = child.child_by_field_name("module_name")
            if module:
                imports.append(_node_text(module))
    return imports


def _python_module_name(rel_path: str) -> Optional[str]:
    if not rel_path.endswith(".py"):
        return None
    parts = rel_path[:-3].replace("\\", "/").split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else None


def _python_resolve_import(
    import_name: str,
    module_to_file_id: Dict[str, str],
    current_module: Optional[str],
) -> Optional[str]:
    if not import_name.startswith("."):
        return module_to_file_id.get(import_name)
    dots = len(import_name) - len(import_name.lstrip("."))
    suffix = import_name[dots:]
    parts = (current_module or "").split(".")
    base = parts[: max(0, len(parts) - dots)]
    target = ".".join(base + [suffix]) if suffix else ".".join(base)
    return module_to_file_id.get(target)


def make_typescript_config() -> LanguageConfig:
    """Build the shared JavaScript/TypeScript parser configuration."""

    loaders = {
        ".ts": LanguageLoader("tree_sitter_typescript", "language_typescript"),
        ".tsx": LanguageLoader("tree_sitter_typescript", "language_tsx"),
        ".js": LanguageLoader("tree_sitter_javascript"),
        ".jsx": LanguageLoader("tree_sitter_javascript"),
        ".mjs": LanguageLoader("tree_sitter_javascript"),
        ".cjs": LanguageLoader("tree_sitter_javascript"),
    }
    return LanguageConfig(
        name="typescript",
        extensions=tuple(loaders),
        lang_map=LazyLanguageMap(loaders),
        class_types=frozenset(
            {
                "class_declaration",
                "interface_declaration",
                "enum_declaration",
                "type_alias_declaration",
            }
        ),
        function_types=frozenset({"function_declaration", "method_definition"}),
        call_types=frozenset({"call_expression", "new_expression"}),
        function_boundary_types=frozenset(
            {"function_declaration", "arrow_function", "method_definition"}
        ),
        export_type="export_statement",
        lambda_decl_types=frozenset({"lexical_declaration", "variable_declaration"}),
        lambda_value_type="arrow_function",
        heritage_type="class_heritage",
        heritage_clause_types=frozenset({"extends_clause", "implements_clause"}),
        extra_excluded_dirs=frozenset({"node_modules"}),
        test_dir_prefixes=("tests/", "test/", "__tests__/"),
        test_file_suffixes=(".test", ".spec"),
        test_name_prefixes=("test", "it", "describe"),
        index_filenames=("index.ts", "index.tsx", "index.js", "index.jsx"),
        extract_imports_fn=_typescript_extract_imports,
        module_name_fn=_typescript_module_name,
        resolve_import_fn=_typescript_resolve_import,
    )


def make_python_config() -> LanguageConfig:
    return LanguageConfig(
        name="python",
        extensions=(".py",),
        lang_map=LazyLanguageMap({".py": LanguageLoader("tree_sitter_python")}),
        class_types=frozenset({"class_definition"}),
        function_types=frozenset({"function_definition"}),
        call_types=frozenset({"call"}),
        function_boundary_types=frozenset({"function_definition"}),
        declaration_wrapper_types=frozenset(
            {
                "if_statement",
                "elif_clause",
                "else_clause",
                "try_statement",
                "except_clause",
                "finally_clause",
                "block",
                "suite",
            }
        ),
        decorator_wrapper_type="decorated_definition",
        superclass_field="superclasses",
        heritage_ident_types=frozenset({"identifier", "type_identifier", "attribute"}),
        test_file_suffixes=("_test",),
        extract_imports_fn=_python_extract_imports,
        module_name_fn=_python_module_name,
        resolve_import_fn=_python_resolve_import,
    )


LANGUAGE_FACTORIES = (make_python_config, make_typescript_config)
ALL_LANGUAGE_FACTORIES = LANGUAGE_FACTORIES
