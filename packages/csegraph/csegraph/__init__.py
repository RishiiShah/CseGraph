"""csegraph v1.7.0 SDK.

Thin facade over the context-engine API from `csegraph-core`
(import namespace: `csegraph_core`). Diagnostic services remain importable from
their module paths under `csegraph_core.graph.*`.
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

__version__ = "1.7.0"

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
