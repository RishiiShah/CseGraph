from __future__ import annotations

import json
import math
from functools import lru_cache
from typing import Any

from csegraph._core.core.models import ContextResponse
from csegraph._core.core.serializer import to_dict

SUPPORTED_ENCODINGS = ("o200k_base", "cl100k_base")
DEFAULT_ENCODING = SUPPORTED_ENCODINGS[0]
MIN_TOKEN_BUDGET = 256
MAX_TOKEN_BUDGET = 16_384

_CHARS_PER_TOKEN = 3


@lru_cache(maxsize=len(SUPPORTED_ENCODINGS))
def _load_encoding(name: str) -> Any | None:
    if name not in SUPPORTED_ENCODINGS:
        choices = ", ".join(SUPPORTED_ENCODINGS)
        raise ValueError(f"encoding must be one of: {choices}")
    try:
        import tiktoken
    except ImportError:
        return None
    return tiktoken.get_encoding(name)


def token_estimator(encoding: str) -> str:
    return "tiktoken" if _load_encoding(encoding) is not None else "chars/3 proxy"


def token_measurement(encoding: str) -> str:
    """Describe whether the reported token count is exact or estimated."""
    return "exact" if _load_encoding(encoding) is not None else "estimated"


def _estimate_tokens(text: str) -> int:
    """Estimate token count conservatively when the optional tokenizer is unavailable."""
    return max(1, math.ceil(len(text) / _CHARS_PER_TOKEN))


def validate_token_budget(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("token_budget must be an integer")
    if value < MIN_TOKEN_BUDGET or value > MAX_TOKEN_BUDGET:
        raise ValueError(f"token_budget must be between {MIN_TOKEN_BUDGET} and {MAX_TOKEN_BUDGET}")
    return value


def serialized_json(payload: Any) -> str:
    return json.dumps(payload, indent=2)


def count_payload_tokens(payload: Any, encoding: str) -> int:
    return count_text_tokens(serialized_json(payload), encoding)


def count_text_tokens(text: str, encoding: str) -> int:
    encoder = _load_encoding(encoding)
    if encoder is not None:
        return len(encoder.encode(text, disallowed_special=()))
    return _estimate_tokens(text)


def response_tokens(response: ContextResponse) -> int:
    return count_payload_tokens(to_dict(response), DEFAULT_ENCODING)
