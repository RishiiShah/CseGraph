"""Language config factories for tree-sitter-based parsers.

Each factory imports its tree-sitter package lazily so missing packages
raise ImportError at registration time rather than module load time.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Dict, List, Optional, Set

from tree_sitter import Language, Node

from csegraph_core.languages.treesitter.config import LanguageConfig


@dataclass(frozen=True)
class LanguageLoader:
    module: str
    function: str = "language"


@dataclass(frozen=True)
class LanguageSpec:
    name: str
    extensions: tuple[str, ...]
    loaders: Dict[str, LanguageLoader]


def _loaders(module: str, extensions: tuple[str, ...], function: str = "language") -> Dict[str, LanguageLoader]:
    return {ext: LanguageLoader(module, function) for ext in extensions}


LANGUAGE_SPECS = [
    LanguageSpec(
        name="typescript",
        extensions=(".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"),
        loaders={
            ".ts": LanguageLoader("tree_sitter_typescript", "language_typescript"),
            ".tsx": LanguageLoader("tree_sitter_typescript", "language_tsx"),
            ".js": LanguageLoader("tree_sitter_javascript"),
            ".jsx": LanguageLoader("tree_sitter_javascript"),
            ".mjs": LanguageLoader("tree_sitter_javascript"),
            ".cjs": LanguageLoader("tree_sitter_javascript"),
        },
    ),
    LanguageSpec("go", (".go",), _loaders("tree_sitter_go", (".go",))),
    LanguageSpec("rust", (".rs",), _loaders("tree_sitter_rust", (".rs",))),
    LanguageSpec("java", (".java",), _loaders("tree_sitter_java", (".java",))),
    LanguageSpec("c", (".c", ".h"), _loaders("tree_sitter_c", (".c", ".h"))),
    LanguageSpec("cpp", (".cpp", ".cc", ".cxx", ".hpp", ".hxx"), _loaders("tree_sitter_cpp", (".cpp", ".cc", ".cxx", ".hpp", ".hxx"))),
    LanguageSpec("ruby", (".rb",), _loaders("tree_sitter_ruby", (".rb",))),
    LanguageSpec("csharp", (".cs",), _loaders("tree_sitter_c_sharp", (".cs",))),
    LanguageSpec("kotlin", (".kt", ".kts"), _loaders("tree_sitter_kotlin", (".kt", ".kts"))),
    LanguageSpec("groovy", (".groovy",), _loaders("tree_sitter_groovy", (".groovy",))),
    LanguageSpec("scala", (".scala",), _loaders("tree_sitter_scala", (".scala",))),
    LanguageSpec("php", (".php",), _loaders("tree_sitter_php", (".php",), "language_php")),
    LanguageSpec("swift", (".swift",), _loaders("tree_sitter_swift", (".swift",))),
    LanguageSpec("lua", (".lua",), _loaders("tree_sitter_lua", (".lua",))),
    LanguageSpec("zig", (".zig",), _loaders("tree_sitter_zig", (".zig",))),
    LanguageSpec("powershell", (".ps1", ".psm1", ".psd1"), _loaders("tree_sitter_powershell", (".ps1", ".psm1", ".psd1"))),
    LanguageSpec("elixir", (".ex", ".exs"), _loaders("tree_sitter_elixir", (".ex", ".exs"))),
    LanguageSpec("objc", (".m", ".mm"), _loaders("tree_sitter_objc", (".m", ".mm"))),
    LanguageSpec("julia", (".jl",), _loaders("tree_sitter_julia", (".jl",))),
    LanguageSpec("verilog", (".v", ".sv", ".vh", ".svh"), _loaders("tree_sitter_verilog", (".v", ".sv", ".vh", ".svh"))),
    LanguageSpec("fortran", (".f90", ".f", ".f03", ".f08"), _loaders("tree_sitter_fortran", (".f90", ".f", ".f03", ".f08"))),
    LanguageSpec("python", (".py",), _loaders("tree_sitter_python", (".py",))),
]


_LANGUAGE_SPECS_BY_NAME = {spec.name: spec for spec in LANGUAGE_SPECS}


def _spec(name: str) -> LanguageSpec:
    return _LANGUAGE_SPECS_BY_NAME[name]


def _make_lang_map(spec: LanguageSpec) -> Dict[str, Language]:
    lang_map: Dict[str, Language] = {}
    for ext, loader in spec.loaders.items():
        module = import_module(loader.module)
        lang_map[ext] = Language(getattr(module, loader.function)())
    return lang_map

# ---------------------------------------------------------------------------
# Shared import / doc helpers
# ---------------------------------------------------------------------------

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


def _strip_string_quotes(text: str) -> str:
    if len(text) >= 2 and text[0] in ('"', "'", "`") and text[-1] == text[0]:
        return text[1:-1]
    return text


# ---------------------------------------------------------------------------
# TypeScript / JavaScript
# ---------------------------------------------------------------------------

def _ts_extract_imports(root: Node) -> List[str]:
    imports: Set[str] = set()
    for child in root.children:
        if child.type == "import_statement":
            _ts_collect_import(child, imports)
        elif child.type == "export_statement":
            for sub in child.children:
                if sub.type == "import_statement":
                    _ts_collect_import(sub, imports)
        elif child.type in ("lexical_declaration", "variable_declaration"):
            _ts_collect_require(child, imports)
    return sorted(imports)


def _ts_collect_import(node: Node, imports: Set[str]) -> None:
    for child in node.children:
        if child.type == "string":
            path = _string_value(child)
            if path:
                imports.add(path)


def _ts_collect_require(node: Node, imports: Set[str]) -> None:
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
        fn = call.child_by_field_name("function")
        if not fn or _node_text(fn) != "require":
            continue
        args = call.child_by_field_name("arguments")
        if not args:
            continue
        for arg in args.children:
            if arg.type == "string":
                path = _string_value(arg)
                if path:
                    imports.add(path)


def _ts_module_name(rel_path: str) -> Optional[str]:
    _INDEX = ("index.ts", "index.tsx", "index.js", "index.jsx")
    for idx in _INDEX:
        if rel_path.endswith(f"/{idx}") or rel_path == idx:
            return rel_path.rsplit(f"/{idx.split('.')[0]}.", 1)[0].replace("/", ".") if "/" in rel_path else rel_path.rsplit(".", 1)[0].replace("/", ".")
    _EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
    for ext in _EXTS:
        if rel_path.endswith(ext):
            return rel_path[: -len(ext)].replace("/", ".")
    return rel_path.replace("/", ".")


def _ts_resolve_import(
    import_name: str,
    module_to_file_id: Dict[str, str],
    current_module: Optional[str],
) -> Optional[str]:
    if not import_name.startswith("."):
        return None
    if current_module is None:
        return None
    parts = current_module.split(".")
    segs = import_name.split("/")
    base_parts = list(parts[:-1])
    for seg in segs:
        if seg == ".":
            continue
        elif seg == "..":
            if base_parts:
                base_parts.pop()
        else:
            base_parts.append(seg)
    candidate = ".".join(base_parts)
    if candidate in module_to_file_id:
        return module_to_file_id[candidate]
    for suffix in ("/index", ""):
        trial = candidate + suffix.replace("/", ".")
        if trial in module_to_file_id:
            return module_to_file_id[trial]
    return None


def make_typescript_config() -> LanguageConfig:
    return LanguageConfig(
        name="typescript",
        extensions=(".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"),
        lang_map=_make_lang_map(_spec("typescript")),
        class_types=frozenset({
            "class_declaration",
            "interface_declaration",
            "enum_declaration",
            "type_alias_declaration",
        }),
        function_types=frozenset({"function_declaration", "method_definition"}),
        call_types=frozenset({"call_expression", "new_expression"}),
        function_boundary_types=frozenset({
            "function_declaration", "arrow_function", "method_definition",
        }),
        export_type="export_statement",
        lambda_decl_types=frozenset({
            "lexical_declaration", "variable_declaration",
        }),
        lambda_value_type="arrow_function",
        heritage_type="class_heritage",
        heritage_clause_types=frozenset({
            "extends_clause", "implements_clause",
        }),
        extra_excluded_dirs=frozenset({"node_modules"}),
        test_dir_prefixes=("tests/", "test/", "__tests__/"),
        test_file_suffixes=(".test", ".spec"),
        test_name_prefixes=("test", "it", "describe"),
        index_filenames=("index.ts", "index.tsx", "index.js", "index.jsx"),
        extract_imports_fn=_ts_extract_imports,
        module_name_fn=_ts_module_name,
        resolve_import_fn=_ts_resolve_import,
    )


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------

def _go_extract_imports(root: Node) -> List[str]:
    imports: Set[str] = set()
    for child in root.children:
        if child.type == "import_declaration":
            for sub in child.children:
                if sub.type == "import_spec":
                    _go_collect_import_spec(sub, imports)
                elif sub.type == "import_spec_list":
                    for spec in sub.children:
                        if spec.type == "import_spec":
                            _go_collect_import_spec(spec, imports)
                elif sub.type == "interpreted_string_literal":
                    imports.add(_strip_string_quotes(_node_text(sub)))
    return sorted(imports)


def _go_collect_import_spec(node: Node, imports: Set[str]) -> None:
    path_node = node.child_by_field_name("path")
    if path_node:
        imports.add(_strip_string_quotes(_node_text(path_node)))
    else:
        for child in node.children:
            if child.type == "interpreted_string_literal":
                imports.add(_strip_string_quotes(_node_text(child)))


def make_go_config() -> LanguageConfig:
    from csegraph_core.languages.treesitter.parser import _extract_go_doc

    return LanguageConfig(
        name="go",
        extensions=(".go",),
        lang_map=_make_lang_map(_spec("go")),
        class_types=frozenset({"type_spec"}),
        function_types=frozenset({
            "function_declaration", "method_declaration",
        }),
        call_types=frozenset({"call_expression"}),
        function_boundary_types=frozenset({
            "function_declaration", "method_declaration", "func_literal",
        }),
        declaration_wrapper_types=frozenset({"type_declaration"}),
        class_body_field="",
        method_receiver_field="receiver",
        extra_excluded_dirs=frozenset({"vendor"}),
        test_dir_prefixes=("tests/", "test/"),
        test_file_suffixes=("_test",),
        test_name_prefixes=("Test", "Benchmark"),
        extract_imports_fn=_go_extract_imports,
        extract_doc_fn=_extract_go_doc,
        module_name_fn=None,
    )


# ---------------------------------------------------------------------------
# Rust
# ---------------------------------------------------------------------------

def _rust_extract_imports(root: Node) -> List[str]:
    imports: Set[str] = set()
    for child in root.children:
        if child.type == "use_declaration":
            _rust_collect_use(child, imports)
    return sorted(imports)


def _rust_collect_use(node: Node, imports: Set[str]) -> None:
    arg = node.child_by_field_name("argument")
    if arg:
        imports.add(_node_text(arg))
    else:
        for child in node.children:
            if child.type in ("scoped_identifier", "scoped_use_list", "identifier", "use_wildcard"):
                imports.add(_node_text(child))


def _rust_module_name(rel_path: str) -> Optional[str]:
    if rel_path.endswith("/mod.rs"):
        return rel_path[:-7].replace("/", ".")
    if rel_path.endswith("/lib.rs") or rel_path.endswith("/main.rs"):
        stem = rel_path.rsplit("/", 1)[0] if "/" in rel_path else rel_path[:-3]
        return stem.replace("/", ".") if stem else rel_path[:-3]
    if rel_path.endswith(".rs"):
        return rel_path[:-3].replace("/", ".")
    return rel_path.replace("/", ".")


def make_rust_config() -> LanguageConfig:
    from csegraph_core.languages.treesitter.parser import _extract_line_doc

    return LanguageConfig(
        name="rust",
        extensions=(".rs",),
        lang_map=_make_lang_map(_spec("rust")),
        class_types=frozenset({
            "struct_item", "enum_item", "trait_item",
        }),
        function_types=frozenset({"function_item"}),
        call_types=frozenset({"call_expression"}),
        function_boundary_types=frozenset({
            "function_item", "closure_expression",
        }),
        impl_types=frozenset({"impl_item"}),
        impl_type_field="type",
        impl_body_field="body",
        extra_excluded_dirs=frozenset({"target"}),
        test_dir_prefixes=("tests/", "test/"),
        test_file_suffixes=(),
        test_name_prefixes=("test",),
        extract_imports_fn=_rust_extract_imports,
        extract_doc_fn=lambda n, l: _extract_line_doc(n, l, "///"),
        module_name_fn=_rust_module_name,
    )


# ---------------------------------------------------------------------------
# Java
# ---------------------------------------------------------------------------

def _java_extract_imports(root: Node) -> List[str]:
    imports: Set[str] = set()
    for child in root.children:
        if child.type == "import_declaration":
            for sub in child.children:
                if sub.type in ("scoped_identifier", "identifier"):
                    imports.add(_node_text(sub))
    return sorted(imports)


def make_java_config() -> LanguageConfig:
    return LanguageConfig(
        name="java",
        extensions=(".java",),
        lang_map=_make_lang_map(_spec("java")),
        class_types=frozenset({
            "class_declaration",
            "interface_declaration",
            "enum_declaration",
            "annotation_type_declaration",
        }),
        function_types=frozenset({
            "method_declaration", "constructor_declaration",
        }),
        call_types=frozenset({
            "method_invocation", "object_creation_expression",
        }),
        function_boundary_types=frozenset({
            "method_declaration", "constructor_declaration", "lambda_expression",
        }),
        superclass_field="superclass",
        interfaces_field="interfaces",
        heritage_ident_types=frozenset({
            "identifier", "type_identifier", "scoped_type_identifier",
        }),
        extra_excluded_dirs=frozenset({"build", ".gradle", "target", "out"}),
        test_dir_prefixes=("test/", "tests/", "src/test/"),
        test_file_suffixes=("Test", "Tests", "Spec"),
        test_name_prefixes=("test",),
        extract_imports_fn=_java_extract_imports,
        module_name_fn=None,
    )


# ---------------------------------------------------------------------------
# C
# ---------------------------------------------------------------------------

def _c_extract_imports(root: Node) -> List[str]:
    imports: Set[str] = set()
    for child in root.children:
        if child.type == "preproc_include":
            path_node = child.child_by_field_name("path")
            if path_node:
                text = _node_text(path_node)
                text = text.strip("<>\"'")
                if text:
                    imports.add(text)
    return sorted(imports)


def make_c_config() -> LanguageConfig:
    return LanguageConfig(
        name="c",
        extensions=(".c", ".h"),
        lang_map=_make_lang_map(_spec("c")),
        class_types=frozenset({
            "struct_specifier", "enum_specifier", "union_specifier",
        }),
        function_types=frozenset({"function_definition"}),
        call_types=frozenset({"call_expression"}),
        function_boundary_types=frozenset({"function_definition"}),
        class_body_field="body",
        extra_excluded_dirs=frozenset({"build", "cmake-build-debug", "cmake-build-release"}),
        test_dir_prefixes=("tests/", "test/"),
        test_name_prefixes=("test_", "Test"),
        extract_imports_fn=_c_extract_imports,
        extract_doc_fn=lambda n, l: "",
        module_name_fn=None,
    )


def make_cpp_config() -> LanguageConfig:
    return LanguageConfig(
        name="cpp",
        extensions=(".cpp", ".cc", ".cxx", ".hpp", ".hxx"),
        lang_map=_make_lang_map(_spec("cpp")),
        class_types=frozenset({
            "class_specifier", "struct_specifier",
            "enum_specifier", "union_specifier",
        }),
        function_types=frozenset({"function_definition"}),
        call_types=frozenset({"call_expression"}),
        function_boundary_types=frozenset({
            "function_definition", "lambda_expression",
        }),
        class_body_field="body",
        heritage_type="base_class_clause",
        heritage_clause_types=frozenset({"base_class_clause"}),
        heritage_ident_types=frozenset({
            "identifier", "type_identifier", "qualified_identifier",
        }),
        extra_excluded_dirs=frozenset({"build", "cmake-build-debug", "cmake-build-release"}),
        test_dir_prefixes=("tests/", "test/"),
        test_name_prefixes=("test_", "Test", "TEST"),
        extract_imports_fn=_c_extract_imports,
        extract_doc_fn=lambda n, l: "",
        module_name_fn=None,
    )


# ---------------------------------------------------------------------------
# Ruby
# ---------------------------------------------------------------------------

def _ruby_extract_imports(root: Node) -> List[str]:
    imports: Set[str] = set()
    for child in root.children:
        if child.type == "call":
            method = child.child_by_field_name("method")
            if method and _node_text(method) in ("require", "require_relative"):
                args = child.child_by_field_name("arguments")
                if args:
                    for arg in args.children:
                        if arg.type == "string":
                            val = _string_value(arg)
                            if val:
                                imports.add(val)
    return sorted(imports)


def make_ruby_config() -> LanguageConfig:
    from csegraph_core.languages.treesitter.parser import _extract_go_doc

    return LanguageConfig(
        name="ruby",
        extensions=(".rb",),
        lang_map=_make_lang_map(_spec("ruby")),
        class_types=frozenset({"class", "module"}),
        function_types=frozenset({"method", "singleton_method"}),
        call_types=frozenset({"call", "command"}),
        function_boundary_types=frozenset({
            "method", "singleton_method", "lambda", "do_block",
        }),
        superclass_field="superclass",
        heritage_ident_types=frozenset({
            "identifier", "constant", "scope_resolution",
        }),
        extra_excluded_dirs=frozenset({"vendor", "bundle"}),
        test_dir_prefixes=("test/", "tests/", "spec/"),
        test_file_suffixes=("_test", "_spec"),
        test_name_prefixes=("test_",),
        extract_imports_fn=_ruby_extract_imports,
        extract_doc_fn=lambda n, l: _extract_go_doc(n, l),
        module_name_fn=None,
    )


# ---------------------------------------------------------------------------
# C#
# ---------------------------------------------------------------------------

def _csharp_extract_imports(root: Node) -> List[str]:
    imports: Set[str] = set()
    for child in root.children:
        if child.type == "using_directive":
            name_node = child.child_by_field_name("name")
            if name_node:
                imports.add(_node_text(name_node))
            else:
                for sub in child.children:
                    if sub.type in ("identifier", "qualified_name"):
                        imports.add(_node_text(sub))
    return sorted(imports)


def make_csharp_config() -> LanguageConfig:
    from csegraph_core.languages.treesitter.parser import _extract_line_doc

    return LanguageConfig(
        name="csharp",
        extensions=(".cs",),
        lang_map=_make_lang_map(_spec("csharp")),
        class_types=frozenset({
            "class_declaration", "struct_declaration",
            "interface_declaration", "enum_declaration",
            "record_declaration",
        }),
        function_types=frozenset({
            "method_declaration", "constructor_declaration",
        }),
        call_types=frozenset({
            "invocation_expression", "object_creation_expression",
        }),
        function_boundary_types=frozenset({
            "method_declaration", "constructor_declaration",
            "lambda_expression", "anonymous_method_expression",
        }),
        heritage_type="base_list",
        heritage_clause_types=frozenset({"base_list"}),
        heritage_ident_types=frozenset({
            "identifier", "generic_name", "qualified_name",
        }),
        declaration_wrapper_types=frozenset({
            "namespace_declaration", "file_scoped_namespace_declaration",
        }),
        extra_excluded_dirs=frozenset({"bin", "obj", "packages"}),
        test_dir_prefixes=("tests/", "test/", "Tests/"),
        test_file_suffixes=("Test", "Tests", "Spec"),
        test_name_prefixes=("Test",),
        extract_imports_fn=_csharp_extract_imports,
        extract_doc_fn=lambda n, l: _extract_line_doc(n, l, "///"),
        module_name_fn=None,
    )


# ---------------------------------------------------------------------------
# Kotlin
# ---------------------------------------------------------------------------

def _kotlin_extract_imports(root: Node) -> List[str]:
    imports: Set[str] = set()
    for child in root.children:
        if child.type == "import_list":
            for imp in child.children:
                if imp.type == "import_header":
                    for sub in imp.children:
                        if sub.type == "identifier":
                            imports.add(_node_text(sub))
        elif child.type == "import_header":
            for sub in child.children:
                if sub.type == "identifier":
                    imports.add(_node_text(sub))
    return sorted(imports)


def make_kotlin_config() -> LanguageConfig:
    return LanguageConfig(
        name="kotlin",
        extensions=(".kt", ".kts"),
        lang_map=_make_lang_map(_spec("kotlin")),
        class_types=frozenset({
            "class_declaration", "object_declaration",
        }),
        function_types=frozenset({"function_declaration"}),
        class_body_type="class_body",
        call_types=frozenset({"call_expression"}),
        function_boundary_types=frozenset({
            "function_declaration", "lambda_literal",
        }),
        superclass_field="delegation_specifiers",
        heritage_ident_types=frozenset({
            "identifier", "user_type", "type_identifier",
        }),
        extra_excluded_dirs=frozenset({"build", ".gradle", "out"}),
        test_dir_prefixes=("test/", "tests/", "src/test/"),
        test_file_suffixes=("Test", "Tests", "Spec"),
        test_name_prefixes=("test",),
        extract_imports_fn=_kotlin_extract_imports,
        module_name_fn=None,
    )


# ---------------------------------------------------------------------------
# Groovy
# ---------------------------------------------------------------------------

def make_groovy_config() -> LanguageConfig:
    return LanguageConfig(
        name="groovy",
        extensions=(".groovy",),
        lang_map=_make_lang_map(_spec("groovy")),
        class_types=frozenset({"class_declaration"}),
        function_types=frozenset({"method_declaration"}),
        call_types=frozenset({"method_invocation"}),
        function_boundary_types=frozenset({"method_declaration"}),
        extract_imports_fn=None,
        module_name_fn=None,
    )

# ---------------------------------------------------------------------------
# Scala
# ---------------------------------------------------------------------------

def make_scala_config() -> LanguageConfig:
    return LanguageConfig(
        name="scala",
        extensions=(".scala",),
        lang_map=_make_lang_map(_spec("scala")),
        class_types=frozenset({"class_definition", "object_definition", "trait_definition"}),
        function_types=frozenset({"function_definition"}),
        call_types=frozenset({"call_expression"}),
        function_boundary_types=frozenset({"function_definition"}),
        extract_imports_fn=None,
        module_name_fn=None,
    )

# ---------------------------------------------------------------------------
# PHP
# ---------------------------------------------------------------------------

def make_php_config() -> LanguageConfig:
    return LanguageConfig(
        name="php",
        extensions=(".php",),
        lang_map=_make_lang_map(_spec("php")),
        class_types=frozenset({"class_declaration"}),
        function_types=frozenset({"method_declaration", "function_declaration"}),
        call_types=frozenset({"function_call_expression", "method_call_expression"}),
        function_boundary_types=frozenset({"method_declaration", "function_declaration"}),
        extract_imports_fn=None,
        module_name_fn=None,
    )

# ---------------------------------------------------------------------------
# Swift
# ---------------------------------------------------------------------------

def make_swift_config() -> LanguageConfig:
    return LanguageConfig(
        name="swift",
        extensions=(".swift",),
        lang_map=_make_lang_map(_spec("swift")),
        class_types=frozenset({"class_declaration", "struct_declaration", "protocol_declaration"}),
        function_types=frozenset({"function_declaration"}),
        call_types=frozenset({"call_expression"}),
        function_boundary_types=frozenset({"function_declaration"}),
        extract_imports_fn=None,
        module_name_fn=None,
    )

# ---------------------------------------------------------------------------
# Lua
# ---------------------------------------------------------------------------

def make_lua_config() -> LanguageConfig:
    return LanguageConfig(
        name="lua",
        extensions=(".lua",),
        lang_map=_make_lang_map(_spec("lua")),
        class_types=frozenset(),
        function_types=frozenset({"function_declaration"}),
        call_types=frozenset({"function_call"}),
        function_boundary_types=frozenset({"function_declaration"}),
        extract_imports_fn=None,
        module_name_fn=None,
    )

# ---------------------------------------------------------------------------
# Zig
# ---------------------------------------------------------------------------

def make_zig_config() -> LanguageConfig:
    return LanguageConfig(
        name="zig",
        extensions=(".zig",),
        lang_map=_make_lang_map(_spec("zig")),
        class_types=frozenset({"struct_declaration"}),
        function_types=frozenset({"function_declaration"}),
        call_types=frozenset({"call_expression"}),
        function_boundary_types=frozenset({"function_declaration"}),
        extract_imports_fn=None,
        module_name_fn=None,
    )

# ---------------------------------------------------------------------------
# PowerShell
# ---------------------------------------------------------------------------

def make_powershell_config() -> LanguageConfig:
    return LanguageConfig(
        name="powershell",
        extensions=(".ps1", ".psm1", ".psd1"),
        lang_map=_make_lang_map(_spec("powershell")),
        class_types=frozenset({"class_declaration"}),
        function_types=frozenset({"function_declaration"}),
        call_types=frozenset({"command"}),
        function_boundary_types=frozenset({"function_declaration"}),
        extract_imports_fn=None,
        module_name_fn=None,
    )

# ---------------------------------------------------------------------------
# Elixir
# ---------------------------------------------------------------------------

def make_elixir_config() -> LanguageConfig:
    return LanguageConfig(
        name="elixir",
        extensions=(".ex", ".exs"),
        lang_map=_make_lang_map(_spec("elixir")),
        class_types=frozenset({"module"}),
        function_types=frozenset({"call"}),
        call_types=frozenset({"call"}),
        function_boundary_types=frozenset({"call"}),
        extract_imports_fn=None,
        module_name_fn=None,
    )

# ---------------------------------------------------------------------------
# Objective-C
# ---------------------------------------------------------------------------

def make_objc_config() -> LanguageConfig:
    return LanguageConfig(
        name="objc",
        extensions=(".m", ".mm"),
        lang_map=_make_lang_map(_spec("objc")),
        class_types=frozenset({"class_interface", "class_implementation"}),
        function_types=frozenset({"method_definition", "function_definition"}),
        call_types=frozenset({"message_expression", "call_expression"}),
        function_boundary_types=frozenset({"method_definition", "function_definition"}),
        extract_imports_fn=None,
        module_name_fn=None,
    )

# ---------------------------------------------------------------------------
# Julia
# ---------------------------------------------------------------------------

def make_julia_config() -> LanguageConfig:
    return LanguageConfig(
        name="julia",
        extensions=(".jl",),
        lang_map=_make_lang_map(_spec("julia")),
        class_types=frozenset({"struct_definition"}),
        function_types=frozenset({"function_definition"}),
        call_types=frozenset({"call_expression"}),
        function_boundary_types=frozenset({"function_definition"}),
        extract_imports_fn=None,
        module_name_fn=None,
    )

# ---------------------------------------------------------------------------
# Verilog
# ---------------------------------------------------------------------------

def make_verilog_config() -> LanguageConfig:
    return LanguageConfig(
        name="verilog",
        extensions=(".v", ".sv", ".vh", ".svh"),
        lang_map=_make_lang_map(_spec("verilog")),
        class_types=frozenset({"module_declaration"}),
        function_types=frozenset({"function_declaration", "task_declaration"}),
        call_types=frozenset({"tf_call"}),
        function_boundary_types=frozenset({"function_declaration", "task_declaration"}),
        extract_imports_fn=None,
        module_name_fn=None,
    )

# ---------------------------------------------------------------------------
# Fortran
# ---------------------------------------------------------------------------

def make_fortran_config() -> LanguageConfig:
    return LanguageConfig(
        name="fortran",
        extensions=(".f90", ".f", ".f03", ".f08"),
        lang_map=_make_lang_map(_spec("fortran")),
        class_types=frozenset({"module", "program"}),
        function_types=frozenset({"function", "subroutine"}),
        call_types=frozenset({"call_statement"}),
        function_boundary_types=frozenset({"function", "subroutine"}),
        extract_imports_fn=None,
        module_name_fn=None,
    )

# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------

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
    base = parts[:max(0, len(parts) - dots)]
    target = ".".join(base + [suffix]) if suffix else ".".join(base)
    return module_to_file_id.get(target)


def make_python_config() -> LanguageConfig:
    return LanguageConfig(
        name="python",
        extensions=(".py",),
        lang_map=_make_lang_map(_spec("python")),
        class_types=frozenset({"class_definition"}),
        function_types=frozenset({"function_definition"}),
        call_types=frozenset({"call"}),
        function_boundary_types=frozenset({"function_definition"}),
        decorator_wrapper_type="decorated_definition",
        superclass_field="superclasses",
        heritage_ident_types=frozenset({"identifier", "type_identifier", "attribute"}),
        test_dir_prefixes=("tests/", "test/"),
        test_file_suffixes=("_test",),
        test_name_prefixes=("test",),
        extract_imports_fn=_python_extract_imports,
        module_name_fn=_python_module_name,
        resolve_import_fn=_python_resolve_import,
    )


# ---------------------------------------------------------------------------
# Registry of all config factories
# ---------------------------------------------------------------------------

ALL_LANGUAGE_FACTORIES = [
    make_python_config,
    make_typescript_config,
    make_go_config,
    make_rust_config,
    make_java_config,
    make_c_config,
    make_cpp_config,
    make_ruby_config,
    make_csharp_config,
    make_kotlin_config,
    make_groovy_config,
    make_scala_config,
    make_php_config,
    make_swift_config,
    make_lua_config,
    make_zig_config,
    make_powershell_config,
    make_elixir_config,
    make_objc_config,
    make_julia_config,
    make_verilog_config,
    make_fortran_config,
]
