"""csegraph_core - import namespace for the csegraph-core distribution.

The install/distribution name is `csegraph-core`; Python imports use
`csegraph_core` because hyphens are invalid in module names.

This package is the source-of-truth context engine for coding agents:
parser, SQLite index, graph traversal, retrieval, and CSE metrics. The SDK
package (`csegraph`), CLI package (`csegraph-cli`), and optional codegen
add-on (`csegraph-codegen`) depend on this package and never on each other.
"""

__version__ = "1.3.0"

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
from csegraph_core.retrieval.constants import VALID_REASONS
from csegraph_core.retrieval.context import ContextService

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
