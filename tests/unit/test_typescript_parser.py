import pytest
from pathlib import Path

import tree_sitter

from csegraph_core.languages.treesitter.languages import make_typescript_config
from csegraph_core.languages.treesitter.parser import TreeSitterParser


@pytest.fixture
def parser():
    return TreeSitterParser(make_typescript_config())


def _write(root, name, content):
    f = root / name
    f.write_text(content, encoding="utf-8")
    return f


def test_parse_function(tmp_path, parser):
    f = _write(tmp_path, "app.ts", "function greet(name: string): string {\n  return name;\n}\n")
    result = parser.parse(f, tmp_path)
    assert result.language == "typescript"
    assert result.parse_status == "ok"
    assert len(result.symbols) == 1
    sym = result.symbols[0]
    assert sym.kind == "function"
    assert sym.name == "greet"
    assert sym.start_line == 1
    assert sym.end_line == 3


def test_parse_class_with_methods(tmp_path, parser):
    f = _write(tmp_path, "svc.ts", "\n".join([
        "class Svc {",
        "  run(): void {}",
        "  stop(): void {}",
        "}",
        "",
    ]))
    result = parser.parse(f, tmp_path)
    names = [(s.kind, s.name) for s in result.symbols]
    assert ("class", "Svc") in names
    assert ("method", "Svc.run") in names
    assert ("method", "Svc.stop") in names
    method = next(s for s in result.symbols if s.name == "Svc.run")
    assert method.parent_symbol_id == result.symbols[0].node_id


def test_parse_interface_as_class(tmp_path, parser):
    f = _write(tmp_path, "types.ts", "interface Config {\n  port: number;\n}\n")
    result = parser.parse(f, tmp_path)
    assert len(result.symbols) == 1
    assert result.symbols[0].kind == "class"
    assert result.symbols[0].name == "Config"


def test_parse_enum_as_class(tmp_path, parser):
    f = _write(tmp_path, "status.ts", "enum Status {\n  Active,\n  Inactive,\n}\n")
    result = parser.parse(f, tmp_path)
    assert len(result.symbols) == 1
    assert result.symbols[0].kind == "class"
    assert result.symbols[0].name == "Status"


def test_parse_arrow_function(tmp_path, parser):
    f = _write(tmp_path, "fn.ts", "const add = (a: number, b: number) => a + b;\n")
    result = parser.parse(f, tmp_path)
    assert len(result.symbols) == 1
    sym = result.symbols[0]
    assert sym.kind == "function"
    assert sym.name == "add"


def test_parse_exported_function(tmp_path, parser):
    f = _write(tmp_path, "exp.ts", "export function helper(): string {\n  return 'ok';\n}\n")
    result = parser.parse(f, tmp_path)
    assert len(result.symbols) == 1
    assert result.symbols[0].name == "helper"


def test_extract_es_imports(tmp_path, parser):
    f = _write(tmp_path, "app.ts", "import { foo, bar } from './utils';\nimport baz from './baz';\n")
    result = parser.parse(f, tmp_path)
    assert "./utils" in result.imports
    assert "./baz" in result.imports


def test_extract_require_imports(tmp_path, parser):
    f = _write(tmp_path, "app.js", "const x = require('./mod');\n")
    result = parser.parse(f, tmp_path)
    assert "./mod" in result.imports


def test_extract_calls(tmp_path, parser):
    f = _write(tmp_path, "app.ts", "\n".join([
        "function main(): void {",
        "  createUser('alice');",
        "  console.log('done');",
        "}",
        "",
    ]))
    result = parser.parse(f, tmp_path)
    sym = result.symbols[0]
    assert "createUser" in sym.calls
    assert "log" in sym.calls


def test_extract_calls_stops_at_nested_function(tmp_path, parser):
    f = _write(tmp_path, "app.ts", "\n".join([
        "function outer(): void {",
        "  doStuff();",
        "  function inner(): void {",
        "    innerOnly();",
        "  }",
        "}",
        "",
    ]))
    result = parser.parse(f, tmp_path)
    outer = next(s for s in result.symbols if s.name == "outer")
    assert "doStuff" in outer.calls
    assert "innerOnly" not in outer.calls


def test_extract_bases(tmp_path, parser):
    f = _write(tmp_path, "cls.ts", "class Dog extends Animal {\n  bark(): void {}\n}\n")
    result = parser.parse(f, tmp_path)
    cls = next(s for s in result.symbols if s.name == "Dog")
    assert cls.bases == ["Animal"]


def test_extract_jsdoc(tmp_path, parser):
    f = _write(tmp_path, "doc.ts", "\n".join([
        "/**",
        " * Greets a user.",
        " */",
        "function greet(name: string): void {}",
        "",
    ]))
    result = parser.parse(f, tmp_path)
    assert "Greets a user." in result.symbols[0].docstring


def test_iter_files_finds_ts_js(tmp_path, parser):
    _write(tmp_path, "a.ts", "")
    _write(tmp_path, "b.tsx", "")
    _write(tmp_path, "c.js", "")
    _write(tmp_path, "d.jsx", "")
    _write(tmp_path, "e.py", "")
    _write(tmp_path, "f.mjs", "")
    paths = parser.iter_files(tmp_path)
    names = sorted(p.name for p in paths)
    assert names == ["a.ts", "b.tsx", "c.js", "d.jsx", "f.mjs"]


def test_iter_files_skips_node_modules(tmp_path, parser):
    nm = tmp_path / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    _write(nm, "index.js", "")
    _write(tmp_path, "app.ts", "")
    paths = parser.iter_files(tmp_path)
    assert len(paths) == 1
    assert paths[0].name == "app.ts"


def test_module_name_from_relpath(parser):
    assert parser.module_name_from_relpath("src/utils.ts") == "src.utils"
    assert parser.module_name_from_relpath("src/index.ts") == "src"
    assert parser.module_name_from_relpath("app.js") == "app"
    assert parser.module_name_from_relpath("lib/index.js") == "lib"


def test_resolve_local_import(parser):
    mapping = {
        "src.utils": "file::src/utils.ts",
        "src.service": "file::src/service.ts",
        "src": "file::src/index.ts",
    }
    assert parser.resolve_local_import("./utils", mapping, "src.app") == "file::src/utils.ts"
    assert parser.resolve_local_import("lodash", mapping, "src.app") is None


def test_parse_js_file(tmp_path, parser):
    f = _write(tmp_path, "lib.js", "function helper() {\n  return 1;\n}\n")
    result = parser.parse(f, tmp_path)
    assert result.language == "typescript"
    assert len(result.symbols) == 1
    assert result.symbols[0].name == "helper"


def test_parse_tsx_file(tmp_path, parser):
    f = _write(tmp_path, "App.tsx", "\n".join([
        "import React from 'react';",
        "export function App() {",
        "  return <div>hello</div>;",
        "}",
        "",
    ]))
    result = parser.parse(f, tmp_path)
    assert result.parse_status == "ok"
    assert any(s.name == "App" for s in result.symbols)


def test_test_file_detection(tmp_path, parser):
    tests_dir = tmp_path / "__tests__"
    tests_dir.mkdir()
    f = _write(tests_dir, "app.test.ts", "\n".join([
        "function testCreateUser(): void {",
        "  expect(1).toBe(1);",
        "}",
        "",
    ]))
    result = parser.parse(f, tmp_path)
    sym = result.symbols[0]
    assert sym.is_test is True


def test_syntax_error_graceful(tmp_path, parser):
    f = _write(tmp_path, "bad.ts", "function {{{{{ broken")
    result = parser.parse(f, tmp_path)
    assert result.parse_status == "error"
    assert result.parse_error is not None
    assert result.symbols == []
