from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class NodeSummary(BaseModel):
    node_id: str = Field(..., description="Original graph node ID")
    name: str = Field(..., description="Node name")
    node_type: str = Field(..., description="Node type: file, class, function, method")
    file_path: str = Field(..., description="Repository-relative file path")
    summary: str = Field(
        ..., description="Concise summary of the node's purpose and responsibilities"
    )
    key_dependencies: List[str] = Field(
        default_factory=list,
        description="Top external dependencies (other node IDs) this node depends on",
    )
    dependents: List[str] = Field(
        default_factory=list, description="Nodes that depend on this node"
    )


class ContextSlice(BaseModel):
    """Compressed context around a specific part of the graph."""

    anchor_node_id: str = Field(..., description="Central node ID for this context")
    anchor_name: str = Field(..., description="Name of the anchor node")
    radius: int = Field(
        ..., description="Hop distance from anchor (1=direct neighbors, 2=2-hops, etc.)"
    )
    included_nodes: Dict[str, NodeSummary] = Field(
        default_factory=dict, description="Summarized nodes within the context"
    )
    edge_types: Dict[str, int] = Field(
        default_factory=dict, description="Aggregated edge types in this context"
    )
    compressed_size_ratio: float = Field(
        ...,
        description="Ratio of compressed vs original (tokens/lines estimate)",
    )


class CompressedGraph(BaseModel):
    """Compressed representation of a link graph for efficient context retrieval."""

    schema_version: str = Field(
        "compressed-graph-v1",
        description="Schema version for serialized compressed graph output",
    )
    root_dir: str = Field(..., description="Absolute repository root")
    original_graph_size: Dict[str, int] = Field(
        description="Original graph stats: file_count, symbol_count, edge_count"
    )
    node_summaries: Dict[str, NodeSummary] = Field(
        default_factory=dict,
        description="Summary for each node in the graph (keyed by node ID)",
    )
    high_degree_nodes: List[str] = Field(
        default_factory=list,
        description="Node IDs of high-degree hubs (by in/out edges), sorted by degree",
    )
    context_slices: Dict[str, ContextSlice] = Field(
        default_factory=dict,
        description="Pre-computed context slices for key nodes and neighborhoods",
    )
    compression_stats: Dict[str, float] = Field(
        default_factory=dict,
        description="Compression metrics: avg_ratio, max_ratio, total_compressed_size_estimate",
    )
