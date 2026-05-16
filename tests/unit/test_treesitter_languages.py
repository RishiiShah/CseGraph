"""Smoke tests for all tree-sitter-backed languages.

Each test verifies that the generic TreeSitterParser correctly extracts
symbols, calls, imports, and doc comments for a given language config.
"""
import pytest
from pathlib import Path

import tree_sitter
import tree_sitter_c
import tree_sitter_c_sharp
import tree_sitter_cpp
import tree_sitter_go
import tree_sitter_java
import tree_sitter_kotlin
import tree_sitter_ruby
import tree_sitter_rust


def _write(root, name, content):
    f = root / name
    f.write_text(content, encoding="utf-8")
    return f


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------

from csegraph_core.languages.treesitter.languages import make_go_config
from csegraph_core.languages.treesitter.parser import TreeSitterParser


@pytest.fixture
def go_parser():
    return TreeSitterParser(make_go_config())


def test_go_parse_function(tmp_path, go_parser):
    f = _write(tmp_path, "main.go", 'package main\n\nfunc greet(name string) string {\n\treturn name\n}\n')
    result = go_parser.parse(f, tmp_path)
    assert result.language == "go"
    assert result.parse_status == "ok"
    funcs = [s for s in result.symbols if s.kind == "function"]
    assert len(funcs) == 1
    assert funcs[0].name == "greet"


def test_go_parse_struct_and_method(tmp_path, go_parser):
    f = _write(tmp_path, "svc.go", "\n".join([
        "package main",
        "",
        "type Server struct {",
        "\tport int",
        "}",
        "",
        "func (s *Server) Start() error {",
        "\treturn nil",
        "}",
        "",
    ]))
    result = go_parser.parse(f, tmp_path)
    names = [(s.kind, s.name) for s in result.symbols]
    assert ("class", "Server") in names
    assert ("method", "Server.Start") in names


def test_go_extract_imports(tmp_path, go_parser):
    f = _write(tmp_path, "main.go", "\n".join([
        "package main",
        "",
        'import (',
        '\t"fmt"',
        '\t"os"',
        ')',
        "",
        "func main() {}",
        "",
    ]))
    result = go_parser.parse(f, tmp_path)
    assert "fmt" in result.imports
    assert "os" in result.imports


def test_go_extract_calls(tmp_path, go_parser):
    f = _write(tmp_path, "main.go", "\n".join([
        "package main",
        "",
        "func main() {",
        '\tfmt.Println("hello")',
        "\thelper()",
        "}",
        "",
    ]))
    result = go_parser.parse(f, tmp_path)
    fn = result.symbols[0]
    assert "Println" in fn.calls
    assert "helper" in fn.calls


def test_go_doc_comment(tmp_path, go_parser):
    f = _write(tmp_path, "main.go", "\n".join([
        "package main",
        "",
        "// Greet returns a greeting.",
        "func Greet() string {",
        '\treturn "hi"',
        "}",
        "",
    ]))
    result = go_parser.parse(f, tmp_path)
    assert "Greet returns a greeting." in result.symbols[0].docstring


def test_go_iter_files_skips_vendor(tmp_path, go_parser):
    vendor = tmp_path / "vendor" / "pkg"
    vendor.mkdir(parents=True)
    _write(vendor, "lib.go", "package pkg")
    _write(tmp_path, "main.go", "package main")
    paths = go_parser.iter_files(tmp_path)
    assert len(paths) == 1
    assert paths[0].name == "main.go"


def test_go_test_file_detection(tmp_path, go_parser):
    f = _write(tmp_path, "main_test.go", "\n".join([
        "package main",
        "",
        "func TestAdd() {",
        "}",
        "",
    ]))
    result = go_parser.parse(f, tmp_path)
    assert result.symbols[0].is_test is True


# ---------------------------------------------------------------------------
# Rust
# ---------------------------------------------------------------------------

from csegraph_core.languages.treesitter.languages import make_rust_config


@pytest.fixture
def rust_parser():
    return TreeSitterParser(make_rust_config())


def test_rust_parse_function(tmp_path, rust_parser):
    f = _write(tmp_path, "lib.rs", "fn greet(name: &str) -> String {\n    name.to_string()\n}\n")
    result = rust_parser.parse(f, tmp_path)
    assert result.language == "rust"
    funcs = [s for s in result.symbols if s.kind == "function"]
    assert len(funcs) == 1
    assert funcs[0].name == "greet"


def test_rust_parse_struct_and_impl(tmp_path, rust_parser):
    f = _write(tmp_path, "lib.rs", "\n".join([
        "struct Server {",
        "    port: u16,",
        "}",
        "",
        "impl Server {",
        "    fn start(&self) {",
        "    }",
        "}",
        "",
    ]))
    result = rust_parser.parse(f, tmp_path)
    names = [(s.kind, s.name) for s in result.symbols]
    assert ("class", "Server") in names
    assert ("method", "Server.start") in names


def test_rust_parse_enum_and_trait(tmp_path, rust_parser):
    f = _write(tmp_path, "lib.rs", "\n".join([
        "enum Color {",
        "    Red,",
        "    Blue,",
        "}",
        "",
        "trait Drawable {",
        "    fn draw(&self);",
        "}",
        "",
    ]))
    result = rust_parser.parse(f, tmp_path)
    names = [(s.kind, s.name) for s in result.symbols]
    assert ("class", "Color") in names
    assert ("class", "Drawable") in names


def test_rust_extract_imports(tmp_path, rust_parser):
    f = _write(tmp_path, "lib.rs", "\n".join([
        "use std::collections::HashMap;",
        "use crate::utils;",
        "",
        "fn main() {}",
        "",
    ]))
    result = rust_parser.parse(f, tmp_path)
    assert any("HashMap" in i for i in result.imports)
    assert any("utils" in i for i in result.imports)


def test_rust_extract_calls(tmp_path, rust_parser):
    f = _write(tmp_path, "lib.rs", "\n".join([
        "fn main() {",
        '    println!("hello");',
        "    helper();",
        "}",
        "",
    ]))
    result = rust_parser.parse(f, tmp_path)
    fn = result.symbols[0]
    assert "helper" in fn.calls


def test_rust_iter_files_skips_target(tmp_path, rust_parser):
    target = tmp_path / "target" / "debug"
    target.mkdir(parents=True)
    _write(target, "out.rs", "fn x() {}")
    _write(tmp_path, "lib.rs", "fn y() {}")
    paths = rust_parser.iter_files(tmp_path)
    assert len(paths) == 1
    assert paths[0].name == "lib.rs"


# ---------------------------------------------------------------------------
# Java
# ---------------------------------------------------------------------------

from csegraph_core.languages.treesitter.languages import make_java_config


@pytest.fixture
def java_parser():
    return TreeSitterParser(make_java_config())


def test_java_parse_class_with_method(tmp_path, java_parser):
    f = _write(tmp_path, "App.java", "\n".join([
        "public class App {",
        "    public void run() {",
        "    }",
        "}",
        "",
    ]))
    result = java_parser.parse(f, tmp_path)
    assert result.language == "java"
    names = [(s.kind, s.name) for s in result.symbols]
    assert ("class", "App") in names
    assert ("method", "App.run") in names


def test_java_parse_interface(tmp_path, java_parser):
    f = _write(tmp_path, "Repo.java", "\n".join([
        "public interface Repo {",
        "    void save();",
        "}",
        "",
    ]))
    result = java_parser.parse(f, tmp_path)
    assert any(s.kind == "class" and s.name == "Repo" for s in result.symbols)


def test_java_extract_imports(tmp_path, java_parser):
    f = _write(tmp_path, "App.java", "\n".join([
        "import java.util.List;",
        "import java.util.Map;",
        "",
        "public class App {}",
        "",
    ]))
    result = java_parser.parse(f, tmp_path)
    assert any("List" in i for i in result.imports)
    assert any("Map" in i for i in result.imports)


def test_java_extract_calls(tmp_path, java_parser):
    f = _write(tmp_path, "App.java", "\n".join([
        "public class App {",
        "    public void run() {",
        '        System.out.println("hi");',
        "        helper();",
        "    }",
        "}",
        "",
    ]))
    result = java_parser.parse(f, tmp_path)
    method = next(s for s in result.symbols if s.kind == "method")
    assert "helper" in method.calls


def test_java_inheritance(tmp_path, java_parser):
    f = _write(tmp_path, "Dog.java", "\n".join([
        "public class Dog extends Animal implements Runnable {",
        "}",
        "",
    ]))
    result = java_parser.parse(f, tmp_path)
    cls = next(s for s in result.symbols if s.name == "Dog")
    assert "Animal" in cls.bases


# ---------------------------------------------------------------------------
# C
# ---------------------------------------------------------------------------

from csegraph_core.languages.treesitter.languages import make_c_config


@pytest.fixture
def c_parser():
    return TreeSitterParser(make_c_config())


def test_c_parse_function(tmp_path, c_parser):
    f = _write(tmp_path, "main.c", "int main() {\n    return 0;\n}\n")
    result = c_parser.parse(f, tmp_path)
    assert result.language == "c"
    assert len(result.symbols) == 1
    assert result.symbols[0].name == "main"
    assert result.symbols[0].kind == "function"


def test_c_parse_struct(tmp_path, c_parser):
    f = _write(tmp_path, "point.c", "\n".join([
        "struct Point {",
        "    int x;",
        "    int y;",
        "};",
        "",
    ]))
    result = c_parser.parse(f, tmp_path)
    assert any(s.kind == "class" and s.name == "Point" for s in result.symbols)


def test_c_extract_includes(tmp_path, c_parser):
    f = _write(tmp_path, "main.c", "\n".join([
        '#include <stdio.h>',
        '#include "utils.h"',
        "",
        "int main() { return 0; }",
        "",
    ]))
    result = c_parser.parse(f, tmp_path)
    assert "stdio.h" in result.imports
    assert "utils.h" in result.imports


def test_c_extract_calls(tmp_path, c_parser):
    f = _write(tmp_path, "main.c", "\n".join([
        "int main() {",
        '    printf("hello");',
        "    return 0;",
        "}",
        "",
    ]))
    result = c_parser.parse(f, tmp_path)
    assert "printf" in result.symbols[0].calls


# ---------------------------------------------------------------------------
# C++
# ---------------------------------------------------------------------------

from csegraph_core.languages.treesitter.languages import make_cpp_config


@pytest.fixture
def cpp_parser():
    return TreeSitterParser(make_cpp_config())


def test_cpp_parse_class_with_method(tmp_path, cpp_parser):
    f = _write(tmp_path, "svc.cpp", "\n".join([
        "class Service {",
        "public:",
        "    void run() {",
        "    }",
        "};",
        "",
    ]))
    result = cpp_parser.parse(f, tmp_path)
    assert result.language == "cpp"
    names = [(s.kind, s.name) for s in result.symbols]
    assert ("class", "Service") in names
    assert ("method", "Service.run") in names


def test_cpp_parse_function(tmp_path, cpp_parser):
    f = _write(tmp_path, "main.cpp", "int main() {\n    return 0;\n}\n")
    result = cpp_parser.parse(f, tmp_path)
    funcs = [s for s in result.symbols if s.kind == "function"]
    assert len(funcs) == 1
    assert funcs[0].name == "main"


# ---------------------------------------------------------------------------
# Ruby
# ---------------------------------------------------------------------------

from csegraph_core.languages.treesitter.languages import make_ruby_config


@pytest.fixture
def ruby_parser():
    return TreeSitterParser(make_ruby_config())


def test_ruby_parse_class_with_method(tmp_path, ruby_parser):
    f = _write(tmp_path, "app.rb", "\n".join([
        "class App",
        "  def run",
        "    puts 'hello'",
        "  end",
        "end",
        "",
    ]))
    result = ruby_parser.parse(f, tmp_path)
    assert result.language == "ruby"
    names = [(s.kind, s.name) for s in result.symbols]
    assert ("class", "App") in names
    assert ("method", "App.run") in names


def test_ruby_extract_imports(tmp_path, ruby_parser):
    f = _write(tmp_path, "app.rb", "\n".join([
        "require 'json'",
        "require_relative 'helper'",
        "",
        "class App; end",
        "",
    ]))
    result = ruby_parser.parse(f, tmp_path)
    assert "json" in result.imports
    assert "helper" in result.imports


def test_ruby_inheritance(tmp_path, ruby_parser):
    f = _write(tmp_path, "dog.rb", "\n".join([
        "class Dog < Animal",
        "  def bark",
        "  end",
        "end",
        "",
    ]))
    result = ruby_parser.parse(f, tmp_path)
    cls = next(s for s in result.symbols if s.name == "Dog")
    assert "Animal" in cls.bases


# ---------------------------------------------------------------------------
# C#
# ---------------------------------------------------------------------------

from csegraph_core.languages.treesitter.languages import make_csharp_config


@pytest.fixture
def csharp_parser():
    return TreeSitterParser(make_csharp_config())


def test_csharp_parse_class_with_method(tmp_path, csharp_parser):
    f = _write(tmp_path, "App.cs", "\n".join([
        "using System;",
        "",
        "namespace MyApp {",
        "    public class App {",
        "        public void Run() {",
        "        }",
        "    }",
        "}",
        "",
    ]))
    result = csharp_parser.parse(f, tmp_path)
    assert result.language == "csharp"
    names = [(s.kind, s.name) for s in result.symbols]
    assert ("class", "App") in names
    assert ("method", "App.Run") in names


def test_csharp_extract_imports(tmp_path, csharp_parser):
    f = _write(tmp_path, "App.cs", "\n".join([
        "using System;",
        "using System.Collections.Generic;",
        "",
        "public class App {}",
        "",
    ]))
    result = csharp_parser.parse(f, tmp_path)
    assert any("System" in i for i in result.imports)


# ---------------------------------------------------------------------------
# Kotlin
# ---------------------------------------------------------------------------

from csegraph_core.languages.treesitter.languages import make_kotlin_config


@pytest.fixture
def kotlin_parser():
    return TreeSitterParser(make_kotlin_config())


def test_kotlin_parse_class_with_method(tmp_path, kotlin_parser):
    f = _write(tmp_path, "App.kt", "\n".join([
        "class App {",
        "    fun run() {",
        "    }",
        "}",
        "",
    ]))
    result = kotlin_parser.parse(f, tmp_path)
    assert result.language == "kotlin"
    names = [(s.kind, s.name) for s in result.symbols]
    assert ("class", "App") in names
    assert ("method", "App.run") in names


def test_kotlin_parse_function(tmp_path, kotlin_parser):
    f = _write(tmp_path, "main.kt", "fun main() {\n    println(\"hello\")\n}\n")
    result = kotlin_parser.parse(f, tmp_path)
    funcs = [s for s in result.symbols if s.kind == "function"]
    assert len(funcs) == 1
    assert funcs[0].name == "main"


def test_fallback_module_name_single_extension_language():
    from csegraph_core.languages.treesitter.languages import make_scala_config

    parser = TreeSitterParser(make_scala_config())
    assert parser.module_name_from_relpath("src/main/App.scala") == "src.main.App"


def test_fallback_module_name_multi_extension_language():
    from csegraph_core.languages.treesitter.languages import make_powershell_config

    parser = TreeSitterParser(make_powershell_config())
    assert parser.module_name_from_relpath("scripts/Profile.psm1") == "scripts.Profile"
