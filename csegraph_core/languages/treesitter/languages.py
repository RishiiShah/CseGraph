"""Language config factories for tree-sitter-based parsers.

Each factory imports its tree-sitter package lazily so missing packages
raise ImportError at registration time rather than module load time.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set

from tree_sitter import Language, Node

from csegraph_core.languages.treesitter.config import LanguageConfig

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
    import tree_sitter_javascript as tsj
    import tree_sitter_typescript as tst

    return LanguageConfig(
        name="typescript",
        extensions=(".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"),
        lang_map={
            ".ts": Language(tst.language_typescript()),
            ".tsx": Language(tst.language_tsx()),
            ".js": Language(tsj.language()),
            ".jsx": Language(tsj.language()),
            ".mjs": Language(tsj.language()),
            ".cjs": Language(tsj.language()),
        },
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


def _go_module_name(rel_path: str) -> Optional[str]:
    if rel_path.endswith(".go"):
        return rel_path[:-3].replace("/", ".")
    return rel_path.replace("/", ".")


def make_go_config() -> LanguageConfig:
    import tree_sitter_go as tsg
    from csegraph_core.languages.treesitter.parser import _extract_go_doc

    return LanguageConfig(
        name="go",
        extensions=(".go",),
        lang_map={".go": Language(tsg.language())},
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
        module_name_fn=_go_module_name,
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
    import tree_sitter_rust as tsr
    from csegraph_core.languages.treesitter.parser import _extract_line_doc

    return LanguageConfig(
        name="rust",
        extensions=(".rs",),
        lang_map={".rs": Language(tsr.language())},
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


def _java_module_name(rel_path: str) -> Optional[str]:
    if rel_path.endswith(".java"):
        return rel_path[:-5].replace("/", ".")
    return rel_path.replace("/", ".")


def make_java_config() -> LanguageConfig:
    import tree_sitter_java as tsj

    return LanguageConfig(
        name="java",
        extensions=(".java",),
        lang_map={".java": Language(tsj.language())},
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
        module_name_fn=_java_module_name,
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


def _c_module_name(rel_path: str) -> Optional[str]:
    for ext in (".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hxx"):
        if rel_path.endswith(ext):
            return rel_path[: -len(ext)].replace("/", ".")
    return rel_path.replace("/", ".")


def make_c_config() -> LanguageConfig:
    import tree_sitter_c as tsc

    return LanguageConfig(
        name="c",
        extensions=(".c", ".h"),
        lang_map={
            ".c": Language(tsc.language()),
            ".h": Language(tsc.language()),
        },
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
        module_name_fn=_c_module_name,
    )


def make_cpp_config() -> LanguageConfig:
    import tree_sitter_cpp as tscpp

    lang = Language(tscpp.language())
    return LanguageConfig(
        name="cpp",
        extensions=(".cpp", ".cc", ".cxx", ".hpp", ".hxx"),
        lang_map={
            ".cpp": lang,
            ".cc": lang,
            ".cxx": lang,
            ".hpp": lang,
            ".hxx": lang,
        },
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
        module_name_fn=_c_module_name,
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


def _ruby_module_name(rel_path: str) -> Optional[str]:
    if rel_path.endswith(".rb"):
        return rel_path[:-3].replace("/", ".")
    return rel_path.replace("/", ".")


def make_ruby_config() -> LanguageConfig:
    import tree_sitter_ruby as tsrb
    from csegraph_core.languages.treesitter.parser import _extract_go_doc

    return LanguageConfig(
        name="ruby",
        extensions=(".rb",),
        lang_map={".rb": Language(tsrb.language())},
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
        module_name_fn=_ruby_module_name,
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


def _csharp_module_name(rel_path: str) -> Optional[str]:
    if rel_path.endswith(".cs"):
        return rel_path[:-3].replace("/", ".")
    return rel_path.replace("/", ".")


def make_csharp_config() -> LanguageConfig:
    import tree_sitter_c_sharp as tscs
    from csegraph_core.languages.treesitter.parser import _extract_line_doc

    return LanguageConfig(
        name="csharp",
        extensions=(".cs",),
        lang_map={".cs": Language(tscs.language())},
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
        module_name_fn=_csharp_module_name,
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


def _kotlin_module_name(rel_path: str) -> Optional[str]:
    for ext in (".kt", ".kts"):
        if rel_path.endswith(ext):
            return rel_path[: -len(ext)].replace("/", ".")
    return rel_path.replace("/", ".")


def make_kotlin_config() -> LanguageConfig:
    import tree_sitter_kotlin as tsk

    return LanguageConfig(
        name="kotlin",
        extensions=(".kt", ".kts"),
        lang_map={
            ".kt": Language(tsk.language()),
            ".kts": Language(tsk.language()),
        },
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
        module_name_fn=_kotlin_module_name,
    )


# ---------------------------------------------------------------------------
# Groovy
# ---------------------------------------------------------------------------

def _groovy_extract_imports(root: Node) -> List[str]:
    return []

def _groovy_module_name(rel_path: str) -> Optional[str]:
    if rel_path.endswith(".groovy"):
        return rel_path[:-7].replace("/", ".")
    return rel_path.replace("/", ".")

def make_groovy_config() -> LanguageConfig:
    import tree_sitter_groovy as tsg
    return LanguageConfig(
        name="groovy",
        extensions=(".groovy",),
        lang_map={".groovy": Language(tsg.language())},
        class_types=frozenset({"class_declaration"}),
        function_types=frozenset({"method_declaration"}),
        call_types=frozenset({"method_invocation"}),
        function_boundary_types=frozenset({"method_declaration"}),
        extract_imports_fn=_groovy_extract_imports,
        module_name_fn=_groovy_module_name,
    )

# ---------------------------------------------------------------------------
# Scala
# ---------------------------------------------------------------------------

def _scala_extract_imports(root: Node) -> List[str]:
    return []

def _scala_module_name(rel_path: str) -> Optional[str]:
    if rel_path.endswith(".scala"):
        return rel_path[:-6].replace("/", ".")
    return rel_path.replace("/", ".")

def make_scala_config() -> LanguageConfig:
    import tree_sitter_scala as tss
    return LanguageConfig(
        name="scala",
        extensions=(".scala",),
        lang_map={".scala": Language(tss.language())},
        class_types=frozenset({"class_definition", "object_definition", "trait_definition"}),
        function_types=frozenset({"function_definition"}),
        call_types=frozenset({"call_expression"}),
        function_boundary_types=frozenset({"function_definition"}),
        extract_imports_fn=_scala_extract_imports,
        module_name_fn=_scala_module_name,
    )

# ---------------------------------------------------------------------------
# PHP
# ---------------------------------------------------------------------------

def _php_extract_imports(root: Node) -> List[str]:
    return []

def _php_module_name(rel_path: str) -> Optional[str]:
    if rel_path.endswith(".php"):
        return rel_path[:-4].replace("/", ".")
    return rel_path.replace("/", ".")

def make_php_config() -> LanguageConfig:
    import tree_sitter_php as tsp
    return LanguageConfig(
        name="php",
        extensions=(".php",),
        lang_map={".php": Language(tsp.language_php())},
        class_types=frozenset({"class_declaration"}),
        function_types=frozenset({"method_declaration", "function_declaration"}),
        call_types=frozenset({"function_call_expression", "method_call_expression"}),
        function_boundary_types=frozenset({"method_declaration", "function_declaration"}),
        extract_imports_fn=_php_extract_imports,
        module_name_fn=_php_module_name,
    )

# ---------------------------------------------------------------------------
# Swift
# ---------------------------------------------------------------------------

def _swift_extract_imports(root: Node) -> List[str]:
    return []

def _swift_module_name(rel_path: str) -> Optional[str]:
    if rel_path.endswith(".swift"):
        return rel_path[:-6].replace("/", ".")
    return rel_path.replace("/", ".")

def make_swift_config() -> LanguageConfig:
    import tree_sitter_swift as tssw
    return LanguageConfig(
        name="swift",
        extensions=(".swift",),
        lang_map={".swift": Language(tssw.language())},
        class_types=frozenset({"class_declaration", "struct_declaration", "protocol_declaration"}),
        function_types=frozenset({"function_declaration"}),
        call_types=frozenset({"call_expression"}),
        function_boundary_types=frozenset({"function_declaration"}),
        extract_imports_fn=_swift_extract_imports,
        module_name_fn=_swift_module_name,
    )

# ---------------------------------------------------------------------------
# Lua
# ---------------------------------------------------------------------------

def _lua_extract_imports(root: Node) -> List[str]:
    return []

def _lua_module_name(rel_path: str) -> Optional[str]:
    if rel_path.endswith(".lua"):
        return rel_path[:-4].replace("/", ".")
    return rel_path.replace("/", ".")

def make_lua_config() -> LanguageConfig:
    import tree_sitter_lua as tsl
    return LanguageConfig(
        name="lua",
        extensions=(".lua",),
        lang_map={".lua": Language(tsl.language())},
        class_types=frozenset(),
        function_types=frozenset({"function_declaration"}),
        call_types=frozenset({"function_call"}),
        function_boundary_types=frozenset({"function_declaration"}),
        extract_imports_fn=_lua_extract_imports,
        module_name_fn=_lua_module_name,
    )

# ---------------------------------------------------------------------------
# Zig
# ---------------------------------------------------------------------------

def _zig_extract_imports(root: Node) -> List[str]:
    return []

def _zig_module_name(rel_path: str) -> Optional[str]:
    if rel_path.endswith(".zig"):
        return rel_path[:-4].replace("/", ".")
    return rel_path.replace("/", ".")

def make_zig_config() -> LanguageConfig:
    import tree_sitter_zig as tsz
    return LanguageConfig(
        name="zig",
        extensions=(".zig",),
        lang_map={".zig": Language(tsz.language())},
        class_types=frozenset({"struct_declaration"}),
        function_types=frozenset({"function_declaration"}),
        call_types=frozenset({"call_expression"}),
        function_boundary_types=frozenset({"function_declaration"}),
        extract_imports_fn=_zig_extract_imports,
        module_name_fn=_zig_module_name,
    )

# ---------------------------------------------------------------------------
# PowerShell
# ---------------------------------------------------------------------------

def _powershell_extract_imports(root: Node) -> List[str]:
    return []

def _powershell_module_name(rel_path: str) -> Optional[str]:
    for ext in (".ps1", ".psm1", ".psd1"):
        if rel_path.endswith(ext):
            return rel_path[: -len(ext)].replace("/", ".")
    return rel_path.replace("/", ".")

def make_powershell_config() -> LanguageConfig:
    import tree_sitter_powershell as tsps
    return LanguageConfig(
        name="powershell",
        extensions=(".ps1", ".psm1", ".psd1"),
        lang_map={
            ".ps1": Language(tsps.language()),
            ".psm1": Language(tsps.language()),
            ".psd1": Language(tsps.language()),
        },
        class_types=frozenset({"class_declaration"}),
        function_types=frozenset({"function_declaration"}),
        call_types=frozenset({"command"}),
        function_boundary_types=frozenset({"function_declaration"}),
        extract_imports_fn=_powershell_extract_imports,
        module_name_fn=_powershell_module_name,
    )

# ---------------------------------------------------------------------------
# Elixir
# ---------------------------------------------------------------------------

def _elixir_extract_imports(root: Node) -> List[str]:
    return []

def _elixir_module_name(rel_path: str) -> Optional[str]:
    for ext in (".ex", ".exs"):
        if rel_path.endswith(ext):
            return rel_path[: -len(ext)].replace("/", ".")
    return rel_path.replace("/", ".")

def make_elixir_config() -> LanguageConfig:
    import tree_sitter_elixir as tse
    return LanguageConfig(
        name="elixir",
        extensions=(".ex", ".exs"),
        lang_map={
            ".ex": Language(tse.language()),
            ".exs": Language(tse.language()),
        },
        class_types=frozenset({"module"}),
        function_types=frozenset({"call"}),
        call_types=frozenset({"call"}),
        function_boundary_types=frozenset({"call"}),
        extract_imports_fn=_elixir_extract_imports,
        module_name_fn=_elixir_module_name,
    )

# ---------------------------------------------------------------------------
# Objective-C
# ---------------------------------------------------------------------------

def _objc_extract_imports(root: Node) -> List[str]:
    return []

def _objc_module_name(rel_path: str) -> Optional[str]:
    for ext in (".m", ".mm"):
        if rel_path.endswith(ext):
            return rel_path[: -len(ext)].replace("/", ".")
    return rel_path.replace("/", ".")

def make_objc_config() -> LanguageConfig:
    import tree_sitter_objc as tso
    return LanguageConfig(
        name="objc",
        extensions=(".m", ".mm"),
        lang_map={
            ".m": Language(tso.language()),
            ".mm": Language(tso.language()),
        },
        class_types=frozenset({"class_interface", "class_implementation"}),
        function_types=frozenset({"method_definition", "function_definition"}),
        call_types=frozenset({"message_expression", "call_expression"}),
        function_boundary_types=frozenset({"method_definition", "function_definition"}),
        extract_imports_fn=_objc_extract_imports,
        module_name_fn=_objc_module_name,
    )

# ---------------------------------------------------------------------------
# Julia
# ---------------------------------------------------------------------------

def _julia_extract_imports(root: Node) -> List[str]:
    return []

def _julia_module_name(rel_path: str) -> Optional[str]:
    if rel_path.endswith(".jl"):
        return rel_path[:-3].replace("/", ".")
    return rel_path.replace("/", ".")

def make_julia_config() -> LanguageConfig:
    import tree_sitter_julia as tsju
    return LanguageConfig(
        name="julia",
        extensions=(".jl",),
        lang_map={".jl": Language(tsju.language())},
        class_types=frozenset({"struct_definition"}),
        function_types=frozenset({"function_definition"}),
        call_types=frozenset({"call_expression"}),
        function_boundary_types=frozenset({"function_definition"}),
        extract_imports_fn=_julia_extract_imports,
        module_name_fn=_julia_module_name,
    )

# ---------------------------------------------------------------------------
# Verilog
# ---------------------------------------------------------------------------

def _verilog_extract_imports(root: Node) -> List[str]:
    return []

def _verilog_module_name(rel_path: str) -> Optional[str]:
    for ext in (".v", ".sv", ".vh", ".svh"):
        if rel_path.endswith(ext):
            return rel_path[: -len(ext)].replace("/", ".")
    return rel_path.replace("/", ".")

def make_verilog_config() -> LanguageConfig:
    import tree_sitter_verilog as tsv
    return LanguageConfig(
        name="verilog",
        extensions=(".v", ".sv", ".vh", ".svh"),
        lang_map={
            ".v": Language(tsv.language()),
            ".sv": Language(tsv.language()),
            ".vh": Language(tsv.language()),
            ".svh": Language(tsv.language()),
        },
        class_types=frozenset({"module_declaration"}),
        function_types=frozenset({"function_declaration", "task_declaration"}),
        call_types=frozenset({"tf_call"}),
        function_boundary_types=frozenset({"function_declaration", "task_declaration"}),
        extract_imports_fn=_verilog_extract_imports,
        module_name_fn=_verilog_module_name,
    )

# ---------------------------------------------------------------------------
# Fortran
# ---------------------------------------------------------------------------

def _fortran_extract_imports(root: Node) -> List[str]:
    return []

def _fortran_module_name(rel_path: str) -> Optional[str]:
    for ext in (".f90", ".f", ".f03", ".f08"):
        if rel_path.endswith(ext):
            return rel_path[: -len(ext)].replace("/", ".")
    return rel_path.replace("/", ".")

def make_fortran_config() -> LanguageConfig:
    import tree_sitter_fortran as tsf
    return LanguageConfig(
        name="fortran",
        extensions=(".f90", ".f", ".f03", ".f08"),
        lang_map={
            ".f90": Language(tsf.language()),
            ".f": Language(tsf.language()),
            ".f03": Language(tsf.language()),
            ".f08": Language(tsf.language()),
        },
        class_types=frozenset({"module", "program"}),
        function_types=frozenset({"function", "subroutine"}),
        call_types=frozenset({"call_statement"}),
        function_boundary_types=frozenset({"function", "subroutine"}),
        extract_imports_fn=_fortran_extract_imports,
        module_name_fn=_fortran_module_name,
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
    import tree_sitter_python as tsp

    return LanguageConfig(
        name="python",
        extensions=(".py",),
        lang_map={".py": Language(tsp.language())},
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
