"""Tests for Python parsed via TreeSitterParser + make_python_config()."""

from __future__ import annotations


def _get_python_parser():
    from csegraph._core.languages import registry

    return registry.for_extension(".py")


def test_python_treesitter_parser_extracts_top_level_functions(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    path = repo / "sample.py"
    path.write_text(
        "\n".join(
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
        ),
        encoding="utf-8",
    )

    parsed = _get_python_parser().parse(path, repo)
    assert [sym.name for sym in parsed.symbols] == ["one", "two", "three"]


def test_python_treesitter_parser_assigns_calls_to_nearest_symbol(tmp_path):
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

    parsed = _get_python_parser().parse(path, repo)
    by_name = {sym.name: sym for sym in parsed.symbols}

    assert by_name["Service"].calls == ["configure"]
    assert by_name["Service.run"].calls == ["helper"]


def test_python_treesitter_parser_is_base_parser():
    from csegraph._core.languages.base import BaseParser

    assert isinstance(_get_python_parser(), BaseParser)


def test_python_treesitter_parser_decorator_start_line(tmp_path):
    """start_line must point to the first decorator, not the def/class keyword."""
    repo = tmp_path / "repo"
    repo.mkdir()
    path = repo / "sample.py"
    path.write_text(
        "\n".join(
            [
                "@property",  # L1 — decorator
                "def x(self):",  # L2
                "    return self._x",  # L3
                "",
                "@staticmethod",  # L5
                "@some_decorator",  # L6
                "def multi(self):",  # L7
                "    pass",  # L8
                "",
                "@dataclass(frozen=True)",  # L10 — decorator with args
                "class Config:",  # L11
                "    x: int = 1",  # L12
                "",
                "def plain():",  # L14 — no decorator
                "    pass",  # L15
            ]
        ),
        encoding="utf-8",
    )

    parsed = _get_python_parser().parse(path, repo)
    by_name = {sym.name: sym for sym in parsed.symbols}

    assert by_name["x"].start_line == 1, "decorator @property must be L1"
    assert by_name["x"].source.startswith("@property")
    assert by_name["multi"].start_line == 5, "first decorator @staticmethod must be L5"
    assert by_name["multi"].source.startswith("@staticmethod")
    assert by_name["Config"].start_line == 10, "@dataclass decorator must be L10"
    assert by_name["Config"].source.startswith("@dataclass")
    assert by_name["plain"].start_line == 14, "no-decorator function unchanged"


def test_python_treesitter_parser_extracts_class_methods(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    path = repo / "sample.py"
    path.write_text(
        "\n".join(
            [
                "class Greeter:",
                "    def greet(self):",
                "        return 'hello'",
                "",
                "    def farewell(self):",
                "        return 'bye'",
                "",
            ]
        ),
        encoding="utf-8",
    )

    parsed = _get_python_parser().parse(path, repo)
    by_name = {sym.name: sym for sym in parsed.symbols}

    assert "Greeter" in by_name
    assert "Greeter.greet" in by_name
    assert "Greeter.farewell" in by_name
    assert by_name["Greeter.greet"].kind == "method"
    assert by_name["Greeter.greet"].parent_symbol_id == by_name["Greeter"].node_id


def test_python_treesitter_parser_records_import_aliases_and_reference_occurrences(tmp_path):
    repo = tmp_path / "repo"
    tests_dir = repo / "tests"
    tests_dir.mkdir(parents=True)
    path = tests_dir / "sample_test.py"
    path.write_text(
        "\n".join(
            [
                "from utils import format_user as fmt, helper",
                "@decorator(arg)",
                "class Child(Base):",
                "    def test_run(self):",
                "        return fmt(helper())",
                "",
            ]
        ),
        encoding="utf-8",
    )

    parsed = _get_python_parser().parse(path, repo)
    import_record = parsed.import_records[0]
    by_name = {sym.name: sym for sym in parsed.symbols}

    assert import_record.metadata["aliases"]["fmt"] == "utils.format_user"
    assert by_name["Child"].references[0].source == "Base"
    test_refs = [
        (ref.kind, ref.name, ref.start_line, ref.end_line, ref.source)
        for ref in by_name["Child.test_run"].references
    ]
    assert ("calls", "fmt", 5, 5, "fmt(helper())") in test_refs
    assert ("calls", "helper", 5, 5, "helper()") in test_refs
    assert ("test", "fmt", 5, 5, "fmt(helper())") in test_refs
