"""Lazy internal facade for the lean CseGraph runtime."""

from __future__ import annotations

from importlib import import_module

__version__ = "2.0.0"

_EXPORTS = {
    "IndexService": ("csegraph._core.index.services", "IndexService"),
    "RefreshService": ("csegraph._core.index.services", "RefreshService"),
    "GraphQueryService": ("csegraph._core.graph.queries", "GraphQueryService"),
    "ContextService": ("csegraph._core.retrieval.context", "ContextService"),
    "MinimalService": ("csegraph._core.retrieval.minimal", "MinimalService"),
    "StatusService": ("csegraph._core.status", "StatusService"),
    "IndexRequiredError": ("csegraph._core.core.errors", "IndexRequiredError"),
    "to_dict": ("csegraph._core.core.serializer", "to_dict"),
}

for _name in (
    "ContextRequest",
    "ContextResponse",
    "ContextSlice",
    "ContextStatus",
    "ContextTarget",
    "GraphResult",
    "IndexResult",
    "MinimalResult",
    "PathResult",
    "RefreshResult",
    "StatusResult",
):
    _EXPORTS[_name] = ("csegraph._core.core.models", _name)

__all__ = ["__version__", *_EXPORTS]


def __getattr__(name: str):
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
