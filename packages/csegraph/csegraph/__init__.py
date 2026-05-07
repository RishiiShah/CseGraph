"""csegraph v1.2.2 SDK.

Thin façade over `csegraph-core` (import namespace: `csegraph_core`).
Adds the LLM-powered `CodegenService`
on top of the core indexing/retrieval primitives. The CLI package
(`csegraph-cli`) imports from `csegraph-core` directly and does not depend
on this package.
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
    SufficiencyMetrics,
    get_profile,
    to_dict,
)
from csegraph_core.core.models import CodegenResult

from csegraph.codegen.service import CodegenService

__version__ = "1.2.2"

# Backward-compat shims: legacy callers import `csegraph.languages.python.parser`,
# `csegraph.legacy.adapters`, `csegraph.core.models`, `csegraph.cse.metrics`,
# etc. Alias every `csegraph_core` submodule under the `csegraph.*` namespace
# so those imports keep working without touching the call sites.
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
    "index",
    "index.loaders",
    "index.repository",
    "index.schema",
    "index.services",
    "index.migrations",
    "languages",
    "languages.python",
    "languages.python.parser",
    "legacy",
    "legacy.adapters",
    "parser",
    "retrieval",
    "retrieval.context",
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
    "CodegenResult",
    "CodegenService",
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
    "SufficiencyMetrics",
    "get_profile",
    "to_dict",
]
