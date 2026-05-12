from __future__ import annotations

from typing import Dict, Optional


class CsegraphError(Exception):
    """Base exception for structured csegraph errors."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        hint: Optional[str] = None,
        install: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.hint = hint
        self.install = install

    def to_payload(self) -> Dict[str, str]:
        payload = {"error": str(self), "error_code": self.error_code}
        if self.hint:
            payload["hint"] = self.hint
        if self.install:
            payload["install"] = self.install
        return payload


class UnsupportedSchemaError(CsegraphError):
    def __init__(self) -> None:
        super().__init__(
            "Unsupported csegraph index schema",
            error_code="unsupported_schema",
            hint="Rebuild the index with the current csegraph-core version.",
        )
