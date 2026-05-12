from pathlib import Path

from csegraph_core.languages.python import parser as python_parser
from csegraph_core.languages.python.parser import PythonParser


def test_python_parser_parses_file_ast_once(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = "\n".join(
        [
            "def one():",
            "    return two()",
            "",
            "def two():",
            "    return three()",
            "",
            "def three():",
            "    return 3",
            "",
        ]
    )
    path = repo / "sample.py"
    path.write_text(source, encoding="utf-8")

    original_parse = python_parser.ast.parse
    calls = []

    def counting_parse(*args, **kwargs):
        calls.append(args[0])
        return original_parse(*args, **kwargs)

    monkeypatch.setattr(python_parser.ast, "parse", counting_parse)

    parsed = PythonParser().parse(path, repo)

    assert [symbol.name for symbol in parsed.symbols] == ["one", "two", "three"]
    assert len(calls) == 1


def test_python_parser_assigns_calls_to_nearest_symbol(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    path = repo / "sample.py"
    path.write_text(
        "\n".join(
            [
                "def helper():",
                "    return 1",
                "",
                "def configure():",
                "    return 2",
                "",
                "class Service:",
                "    value = configure()",
                "",
                "    def run(self):",
                "        return helper()",
                "",
            ]
        ),
        encoding="utf-8",
    )

    parsed = PythonParser().parse(path, repo)
    by_name = {symbol.name: symbol for symbol in parsed.symbols}

    assert by_name["Service"].calls == ["configure"]
    assert by_name["Service.run"].calls == ["helper"]


def test_python_parser_keeps_nested_function_calls_on_emitted_parent(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    path = repo / "sample.py"
    path.write_text(
        "\n".join(
            [
                "def outer():",
                "    def inner():",
                "        return dependency()",
                "    return inner()",
                "",
                "def dependency():",
                "    return 1",
                "",
            ]
        ),
        encoding="utf-8",
    )

    parsed = PythonParser().parse(path, repo)
    by_name = {symbol.name: symbol for symbol in parsed.symbols}

    assert "inner" in by_name["outer"].calls
    assert "dependency" in by_name["outer"].calls
