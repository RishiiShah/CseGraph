from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class SufficiencyQuery(BaseModel):
    """Input to the CSE: what context are we evaluating for?"""

    query_text: str = Field(
        ..., description="Natural-language code generation request"
    )
    target_node_id: str = Field(
        ..., description="Anchor node ID in the link graph"
    )
    target_file_path: str = Field(
        ..., description="Repository-relative path of the file being modified"
    )


class SufficiencyMetrics(BaseModel):
    """The three sufficiency scores computed by the CSE."""

    dependency_completeness: float = Field(
        ...,
        description="Ratio of resolved dependencies in context (0.0-1.0)",
    )
    entity_coverage: float = Field(
        ...,
        description="Ratio of query-mentioned entities found in context (0.0-1.0)",
    )
    semantic_overlap: float = Field(
        ...,
        description="Cosine similarity between query and context (0.0-1.0)",
    )


class SufficiencyResult(BaseModel):
    """Output of the CSE evaluation."""

    schema_version: str = Field(
        "cse-result-v1",
        description="Schema version for serialized CSE output",
    )
    is_sufficient: bool = Field(
        ..., description="Whether the context passed all thresholds"
    )
    metrics: SufficiencyMetrics = Field(
        ..., description="Final metric scores after last evaluation round"
    )
    context_node_ids: List[str] = Field(
        default_factory=list,
        description="Node IDs included in the final context",
    )
    expansion_rounds: int = Field(
        ..., description="Number of expansion rounds executed"
    )
    max_rounds: int = Field(
        ..., description="Maximum expansion rounds allowed"
    )
    thresholds: Dict[str, float] = Field(
        default_factory=dict,
        description="Thresholds used: dep_completeness, entity_coverage, semantic_overlap",
    )
    reason: str = Field(
        ...,
        description="Human-readable explanation of the sufficiency decision",
    )
    query: SufficiencyQuery = Field(
        ..., description="The original query that was evaluated"
    )
