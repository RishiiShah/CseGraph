import json
from unittest.mock import patch

import tiktoken

from csegraph._core.retrieval.token_budget import (
    count_text_tokens,
    token_estimator,
    token_measurement,
)


def test_token_measurement_reports_exact_when_tokenizer_is_available():
    class _Encoder:
        def encode(self, text: str, *, disallowed_special: object = ()) -> list[int]:
            del text, disallowed_special
            return [1, 2, 3]

    with patch(
        "csegraph._core.retrieval.token_budget._load_encoding",
        return_value=_Encoder(),
    ):
        assert token_estimator("o200k_base") == "tiktoken"
        assert token_measurement("o200k_base") == "exact"
        assert count_text_tokens("anything", "o200k_base") == 3


def test_token_measurement_reports_estimated_without_optional_tokenizer():
    with patch(
        "csegraph._core.retrieval.token_budget._load_encoding",
        return_value=None,
    ):
        assert token_estimator("o200k_base") == "chars/3 proxy"
        assert token_measurement("o200k_base") == "estimated"
        assert count_text_tokens("abcdefgh", "o200k_base") == 3


def test_fallback_estimate_covers_representative_serialized_context():
    payload = {
        "schema_version": "csegraph-context-v5",
        "status": "ready",
        "slices": [
            {
                "path": "src/example.py",
                "lines": [1, 14],
                "symbol": "python_target",
                "role": "target",
                "code": (
                    "def python_target(value: str) -> dict[str, str]:\\n"
                    '    message = f"hello {value}"\\n'
                    '    return {"message": message, "kind": "example"}\\n'
                ),
            }
        ],
        "diagnostics": {
            "target": "python_target",
            "next": "inspect callers",
            "token_budget": 400,
        },
    }
    text = json.dumps(payload, indent=2)
    actual = len(tiktoken.get_encoding("o200k_base").encode(text, disallowed_special=()))

    with patch("csegraph._core.retrieval.token_budget._load_encoding", return_value=None):
        assert count_text_tokens(text, "o200k_base") >= actual
