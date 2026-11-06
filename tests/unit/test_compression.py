from __future__ import annotations

import pytest

from csegraph._core.index.ingestion import _file_summary, _symbol_summary
from csegraph._core.languages.registry import registry
from csegraph._core.text.compression import (
    compact_internal,
    compact_json,
    terse_prose,
)


def test_terse_prose_drops_filler_but_keeps_identifiers():
    assert (
        terse_prose("The target symbol does not fit the current budget.")
        == "target symbol does not fit current budget."
    )
    assert terse_prose("Inspect `Cache.invalidate` and app/cache.py.") == (
        "Inspect `Cache.invalidate` and app/cache.py."
    )


def test_terse_prose_preserves_quoted_paths_and_arrows():
    text = "No path: 'greet' ↛ 'unused'."
    assert terse_prose(text) == text


def test_internal_compaction_is_aggressive_but_keeps_symbols():
    assert compact_internal("Module app.py defines a, b, c.") == "Module app.py defines a, b, c"
    assert compact_internal("The function is just a simple wrapper") == (
        "function a simple wrapper"
    )


def test_compact_json_is_canonical_and_separator_minimal():
    assert compact_json({"b": 1, "a": [1, 2]}) == '{"a":[1,2],"b":1}'


@pytest.mark.parametrize("compress", [terse_prose, compact_internal])
@pytest.mark.parametrize(
    "protected",
    [
        '`value = "the  result"`',
        '"the  result , actually"',
        "'the\tresult'",
        '```python\nif value:\n    return "the  result"\n```',
        r'"escaped \" the  result"',
    ],
)
def test_compression_preserves_protected_bytes(compress, protected):
    assert protected in compress(f"Please inspect {protected} before retrying.")


def test_stored_summaries_preserve_signatures_and_stopword_names(tmp_path):
    source = tmp_path / "app.py"
    signature = 'def on(the: str = "the  result") -> str:'
    source.write_text(signature + '\n    """The result is just a value."""\n    return the\n')
    parsed = registry.for_extension(".py").parse(source, tmp_path)

    assert _symbol_summary(parsed.symbols[0]).startswith(signature)
    assert _file_summary(parsed) == "Module app.py defines on"
