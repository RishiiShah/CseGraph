"""csegraph v1.3.2 SDK.

Thin facade over `csegraph-core` (import namespace: `csegraph_core`) for
coding-agent context retrieval. Code generation lives in the optional
`csegraph-codegen` add-on, not in this SDK facade. The CLI package
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
    VALID_REASONS,
    get_profile,
    to_dict,
)

__version__ = "1.3.2"

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
    "languages.base",
    "languages.registry",
    "languages.types",
    "languages.python",
    "languages.python.parser",
    "languages.python.tokenizer",
    "legacy",
    "text",
    "text.entities",
    "text.query_tokenizer",
    "text.tokens",
    "legacy.adapters",
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
    "SufficiencyMetrics",
    "VALID_REASONS",
    "get_profile",
    "to_dict",
]
