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

    def to_payload(self) -> Dict[str, object]:
        payload: Dict[str, object] = {"error": str(self), "error_code": self.error_code}
        if self.hint:
            payload["hint"] = self.hint
        if self.install:
            payload["install"] = self.install
        return payload


class IndexRequiredError(CsegraphError):
    """Raised when an operation requires a fresh compatible index."""

    def __init__(
        self,
        message: str = "A current CseGraph index is required for this repository.",
    ) -> None:
        super().__init__(
            message,
            error_code="index_required",
            hint="Run `csegraph index <repo>` to build a fresh index.",
        )

    def to_payload(self) -> Dict[str, object]:
        payload = super().to_payload()
        payload["next"] = {
            "tool": "csegraph_index",
            "reason": "Build a fresh index before retrying this operation.",
        }
        return payload
