"""Guards for keeping dogfood repository benchmarks out of default pytest."""

from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomlkit


ROOT = Path(__file__).resolve().parents[2]


def _pyproject() -> dict:
    path = ROOT / "pyproject.toml"
    if "tomllib" in globals():
        with path.open("rb") as fh:
            return tomllib.load(fh)
    return tomlkit.parse(path.read_text(encoding="utf-8"))


def test_self_corpus_is_opt_in_not_default_pytest():
    pytest_options = _pyproject()["tool"]["pytest"]["ini_options"]

    assert pytest_options["addopts"] == ["-m", "not self_corpus"]
    assert any(marker.startswith("self_corpus:") for marker in pytest_options["markers"])


def test_self_corpus_test_module_is_marked_without_collection_side_effects():
    module_text = (ROOT / "tests" / "integration" / "test_context_quality_corpus.py").read_text(
        encoding="utf-8"
    )

    assert "pytestmark = pytest.mark.self_corpus" in module_text
    assert 'Path(__file__).resolve().parents[2]' in module_text
    assert 'run_corpus(' in module_text
