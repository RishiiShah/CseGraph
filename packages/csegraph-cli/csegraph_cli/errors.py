from __future__ import annotations

from typing import Dict, Optional

from csegraph_core.core.errors import CsegraphError


class CsegraphCLIError(Exception):
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


def error_payload(exc: Exception) -> Dict[str, str]:
    if isinstance(exc, CsegraphError):
        return exc.to_payload()
    if isinstance(exc, CsegraphCLIError):
        payload = {"error": str(exc), "error_code": exc.error_code}
        if exc.hint:
            payload["hint"] = exc.hint
        if exc.install:
            payload["install"] = exc.install
        return payload
    return {"error": str(exc)}
