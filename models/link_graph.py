from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


NodeType = Literal["file", "function", "class", "method"]
RelationType = Literal["contains", "imports", "calls"]


class GraphNode(BaseModel):
    id: str = Field(..., description="Unique graph node identifier")
    type: NodeType = Field(..., description="Graph node type")
    name: str = Field(..., description="Display name for the node")
    file_path: str = Field(..., description="Repository-relative file path")
    start_line: Optional[int] = Field(None, description="Start line for symbol nodes")
    end_line: Optional[int] = Field(None, description="End line for symbol nodes")


class GraphEdge(BaseModel):
    source: str = Field(..., description="Source node ID")
    target: str = Field(..., description="Target node ID")
    relation: RelationType = Field(..., description="Edge relation type")
    metadata: Optional[Dict[str, str]] = Field(
        None,
        description="Optional metadata for edge resolution details",
    )


class LinkGraphSummary(BaseModel):
    file_count: int = Field(..., description="Total number of file nodes")
    symbol_count: int = Field(..., description="Total number of non-file symbol nodes")
    edge_count: int = Field(..., description="Total number of edges")


class LinkGraph(BaseModel):
    schema_version: str = Field(
        "link-graph-v1",
        description="Schema version for serialized link graph output",
    )
    root_dir: str = Field(..., description="Absolute repository root used to build the graph")
    summary: LinkGraphSummary = Field(..., description="Graph summary statistics")
    nodes: List[GraphNode] = Field(default_factory=list, description="All graph nodes")
    edges: List[GraphEdge] = Field(default_factory=list, description="All graph edges")
