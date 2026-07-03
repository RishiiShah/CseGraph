from unittest.mock import patch

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
        assert token_estimator("o200k_base") == "chars/4 proxy"
        assert token_measurement("o200k_base") == "estimated"
        assert count_text_tokens("abcdefgh", "o200k_base") == 2
