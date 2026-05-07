"""csegraph_core — single source of truth for csegraph storage, parsing, and retrieval.

The SDK package (`csegraph`) and CLI package (`csegraph-cli`) both depend on
this package and never on each other. CodegenService (which carries LLM
dependencies) lives in the SDK, not here.
"""

from csegraph_core.config.profiles import PROFILES, ProfileConfig, get_profile
from csegraph_core.core.models import (
    ContextNode,
    ContextResult,
    GraphEdgeView,
    GraphNodeView,
    GraphResult,
    IndexResult,
    RefreshResult,
    to_dict,
)
from csegraph_core.cse.metrics import SufficiencyMetrics
from csegraph_core.graph.queries import GraphQueryService
from csegraph_core.index.repository import ProjectIndex
from csegraph_core.index.services import IndexService, RefreshService
from csegraph_core.retrieval.context import ContextService

__all__ = [
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
