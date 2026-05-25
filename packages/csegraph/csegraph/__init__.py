"""csegraph v1.7.1 SDK.

Thin facade over the context-engine API from `csegraph-core`
(import namespace: `csegraph_core`). Diagnostic and maintainer services are not
re-exported by this SDK facade; repo-local tooling imports them by module path.
"""
from __future__ import annotations

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
    KeyEntity,
    MinimalResult,
    MinimalService,
    NextToolSuggestion,
    PathEdge,
    PathResult,
    PathStep,
    POSTPROCESS_LEVELS,
    PostprocessResult,
    PostprocessService,
    PROFILES,
    ProfileConfig,
    RefreshResult,
    RefreshService,
    StatusResult,
    StatusService,
    SufficiencyMetrics,
    SufficiencyResult,
    VALID_REASONS,
    get_profile,
    load_profile,
    to_dict,
)

__version__ = "1.7.1"

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
    "KeyEntity",
    "MinimalResult",
    "MinimalService",
    "NextToolSuggestion",
    "PathEdge",
    "PathResult",
    "PathStep",
    "POSTPROCESS_LEVELS",
    "PostprocessResult",
    "PostprocessService",
    "PROFILES",
    "ProfileConfig",
    "RefreshResult",
    "RefreshService",
    "StatusResult",
    "StatusService",
    "SufficiencyMetrics",
    "SufficiencyResult",
    "VALID_REASONS",
    "get_profile",
    "load_profile",
    "to_dict",
]
