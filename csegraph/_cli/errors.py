from __future__ import annotations

from typing import Dict

from csegraph._core.core.errors import CsegraphError


class CsegraphCLIError(CsegraphError):
    pass


def error_payload(exc: Exception) -> Dict[str, str]:
    if isinstance(exc, CsegraphError):
        return exc.to_payload()
    return {"error": str(exc)}
