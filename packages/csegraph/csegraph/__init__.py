"""csegraph v1.4.0 SDK.

Thin facade over `csegraph-core` (import namespace: `csegraph_core`) for
coding-agent context retrieval. The CLI package (`csegraph-cli`) imports
from `csegraph-core` directly and does not depend on this package.
"""
from __future__ import annotations

import sys as _sys

import csegraph_core as _core
from csegraph_core import (
    ContextNode,
    ContextResult,
    ContextService,
    GraphEdgeView,
    GraphNodeView,
    GraphQueryService,
    GraphResult,
    IndexResult,
    IndexService,
    PROFILES,
    ProfileConfig,
    ProjectIndex,
    RefreshResult,
    RefreshService,
    ReportResult,
    ReportService,
    SufficiencyMetrics,
    VALID_REASONS,
    VisualExportResult,
    VisualExportService,
    get_profile,
    to_dict,
)

__version__ = "1.4.0"

# Backward-compat shims: alias supported `csegraph_core` submodules under the
# `csegraph.*` namespace for callers using paths like
# `csegraph.languages.python.parser`, `csegraph.core.models`, and
# `csegraph.cse.metrics`.
for _name in (
    "config",
    "config.profiles",
    "core",
    "core.ids",
    "core.models",
    "cse",
    "cse.metrics",
    "graph",
    "graph.queries",
    "graph.report",
    "graph.visual",
    "index",
    "index.loaders",
    "index.repository",
    "index.schema",
    "index.services",
    "index.migrations",
    "languages",
    "languages.base",
    "languages.registry",
    "languages.types",
    "languages.python",
    "languages.python.parser",
    "languages.python.tokenizer",
    "text",
    "text.entities",
    "text.query_tokenizer",
    "text.source_reader",
    "text.tokens",
    "retrieval",
    "retrieval.constants",
    "retrieval.context",
    "retrieval.explain",
    "retrieval.scoring",
):
    _core_mod = f"csegraph_core.{_name}"
    if _core_mod in _sys.modules:
        _sys.modules[f"csegraph.{_name}"] = _sys.modules[_core_mod]
    else:
        try:
            __import__(_core_mod)
            _sys.modules[f"csegraph.{_name}"] = _sys.modules[_core_mod]
        except ImportError:
            pass

__all__ = [
    "__version__",
    "ContextNode",
    "ContextResult",
    "ContextService",
    "GraphEdgeView",
    "GraphNodeView",
    "GraphQueryService",
    "GraphResult",
    "IndexResult",
    "IndexService",
    "PROFILES",
    "ProfileConfig",
    "ProjectIndex",
    "RefreshResult",
    "RefreshService",
    "ReportResult",
    "ReportService",
    "SufficiencyMetrics",
    "VALID_REASONS",
    "VisualExportResult",
    "VisualExportService",
    "get_profile",
    "to_dict",
]
