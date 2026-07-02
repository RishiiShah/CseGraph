from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

import tiktoken

from csegraph._core.core.models import ContextResponse
from csegraph._core.core.serializer import to_dict

SUPPORTED_ENCODINGS = ("o200k_base", "cl100k_base")
MIN_TOKEN_BUDGET = 256
MAX_TOKEN_BUDGET = 16_384


@lru_cache(maxsize=len(SUPPORTED_ENCODINGS))
def _encoding(name: str) -> Any:
    if name not in SUPPORTED_ENCODINGS:
        choices = ", ".join(SUPPORTED_ENCODINGS)
        raise ValueError(f"encoding must be one of: {choices}")
    return tiktoken.get_encoding(name)


def validate_token_budget(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("token_budget must be an integer")
    if value < MIN_TOKEN_BUDGET or value > MAX_TOKEN_BUDGET:
        raise ValueError(
            f"token_budget must be between {MIN_TOKEN_BUDGET} and {MAX_TOKEN_BUDGET}"
        )
    return value


def serialized_json(payload: Any) -> str:
    return json.dumps(payload, indent=2)


def count_payload_tokens(payload: Any, encoding: str) -> int:
    return len(
        _encoding(encoding).encode(
            serialized_json(payload),
            disallowed_special=(),
        )
    )


def count_text_tokens(text: str, encoding: str) -> int:
    return len(_encoding(encoding).encode(text, disallowed_special=()))


def response_tokens(response: ContextResponse) -> int:
    """Converge the self-reported token count to the serialized MCP payload."""
    encoding = str(response.usage["encoding"])
    response.usage["tokens"] = 0
    for _ in range(8):
        tokens = count_payload_tokens(to_dict(response), encoding)
        if response.usage.get("tokens") == tokens:
            return tokens
        response.usage["tokens"] = tokens
    return int(response.usage["tokens"])


def response_bytes(response: ContextResponse) -> int:
    return len(serialized_json(to_dict(response)).encode("utf-8"))
