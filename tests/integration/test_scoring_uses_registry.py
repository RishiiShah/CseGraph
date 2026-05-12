"""Verify that scoring and metrics dispatch through the registry for source-side tokenization."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

import pytest

from csegraph_core.languages.registry import LanguageRegistry
from csegraph_core.languages.types import ParsedFile


class RecordingTokenizer:
    def __init__(self):
        self.calls: List[str] = []

    def tokenize(self, text: str) -> List[str]:
        self.calls.append(text)
        return text.lower().split()


class FakeParser:
    language = "fakelang"
    extensions = (".fake",)

    def parse(self, path: Path, root: Path) -> ParsedFile:
        raise NotImplementedError

    def iter_files(self, root: Path) -> Iterable[Path]:
        return []

    def module_name_from_relpath(self, rel_path: str):
        return None

    def resolve_local_import(self, import_name, module_to_file_id, current_module):
        return None


@pytest.fixture
def patched_registry(monkeypatch):
    rec = RecordingTokenizer()
    reg = LanguageRegistry()
    reg.register(FakeParser(), rec)
    monkeypatch.setattr("csegraph_core.retrieval.scoring.registry", reg)
    monkeypatch.setattr("csegraph_core.cse.metrics.registry", reg)
    return rec


def _fake_symbols():
    return {
        "sym::fake.fake::function::do_work": {
            "id": "sym::fake.fake::function::do_work",
            "kind": "function",
            "name": "do_work",
            "file_path": "fake.fake",
            "language": "fakelang",
            "signature": "def do_work()",
            "docstring": "Does work",
            "start_line": 1,
            "end_line": 5,
            "source_hash": "abc",
            "metadata": None,
            "parent_symbol_id": None,
        }
    }


def test_lexical_scores_uses_registry_tokenizer_for_source(patched_registry):
    from csegraph_core.retrieval.scoring import lexical_scores
    symbols = _fake_symbols()
    lexical_scores("task text", symbols, summaries={})
    assert len(patched_registry.calls) > 0


def test_compute_metrics_uses_registry_tokenizer_for_source(patched_registry):
    from csegraph_core.cse.metrics import compute_metrics
    symbols = _fake_symbols()
    target_id = list(symbols.keys())[0]
    compute_metrics(
        task="do work",
        target_id=target_id,
        context_ids=[target_id],
        symbols=symbols,
        summaries={},
        outgoing={},
    )
    assert len(patched_registry.calls) > 0


def _unknown_lang_symbols():
    return {
        "sym::x.unkn::function::foo": {
            "id": "sym::x.unkn::function::foo",
            "kind": "function",
            "name": "foo",
            "file_path": "x.unkn",
            "language": "doesnotexist",
            "signature": "def foo()",
            "docstring": "",
            "start_line": 1,
            "end_line": 2,
            "source_hash": "abc",
            "metadata": None,
            "parent_symbol_id": None,
        }
    }


def test_unknown_language_raises_in_lexical_scores():
    """scoring.lexical_scores must raise (not silently fall back) for unknown language."""
    from csegraph_core.languages.registry import UnsupportedLanguageError
    from csegraph_core.retrieval.scoring import lexical_scores
    with pytest.raises(UnsupportedLanguageError):
        lexical_scores("foo task", _unknown_lang_symbols(), summaries={})


def test_unknown_language_raises_in_compute_metrics():
    """metrics.compute_metrics must raise (not silently fall back) for unknown language."""
    from csegraph_core.languages.registry import UnsupportedLanguageError
    from csegraph_core.cse.metrics import compute_metrics
    symbols = _unknown_lang_symbols()
    target_id = list(symbols.keys())[0]
    with pytest.raises(UnsupportedLanguageError):
        compute_metrics(
            task="foo task",
            target_id=target_id,
            context_ids=[target_id],
            symbols=symbols,
            summaries={},
            outgoing={},
        )
