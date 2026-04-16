from typing import List, Optional

from pydantic import BaseModel, Field


class CodeGenResult(BaseModel):
    """Output of the Code Generation Agent."""

    schema_version: str = Field(
        "code-gen-result-v1",
        description="Schema version for serialized code gen output",
    )
    generated_code: str = Field(
        ..., description="LLM-generated source code"
    )
    query_text: str = Field(
        ..., description="Original natural-language task description"
    )
    target_node_id: str = Field(
        ..., description="Target node ID the code was generated for"
    )
    target_file_path: str = Field(
        ..., description="Repository-relative file path of the target"
    )
    model: str = Field(
        ..., description="LLM model identifier used for generation"
    )
    context_nodes_used: List[str] = Field(
        default_factory=list,
        description="Node IDs whose summaries were included in the prompt",
    )
    raw_code_nodes_used: List[str] = Field(
        default_factory=list,
        description="Node IDs whose verbatim source was included (raw code fallback)",
    )
    prompt_tokens: Optional[int] = Field(
        None, description="Tokens consumed by the prompt"
    )
    completion_tokens: Optional[int] = Field(
        None, description="Tokens in the generated completion"
    )
    cse_sufficient: bool = Field(
        ..., description="Whether CSE declared context sufficient before generation"
    )
    cse_rounds: int = Field(
        ..., description="Number of CSE expansion rounds that ran before generation"
    )
    mean_logprob: Optional[float] = Field(
        None, description="Mean log-probability of generated tokens (lower = less confident)"
    )
