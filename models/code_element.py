from pydantic import BaseModel, Field
from typing import List, Optional


class MethodNode(BaseModel):
    name: str = Field(..., description="Method name")
    node_type: str = Field("method", description="Node type for class methods")
    start_line: int = Field(..., description="Starting line number")
    end_line: int = Field(..., description="Ending line number")
    docstring: Optional[str] = Field(None, description="Docstring associated with the method")
    code_content: str = Field(..., description="Method code snippet")

class CodeNode(BaseModel):
    name: str = Field(..., description="Name of the node (e.g., function name, class name)")
    node_type: str = Field(..., description="Type of the node: 'function', 'class', 'method', 'file'")
    file_path: str = Field(..., description="Path to the file containing this node")
    start_line: int = Field(..., description="Starting line number")
    end_line: int = Field(..., description="Ending line number")
    docstring: Optional[str] = Field(None, description="Docstring associated with the node")
    code_content: str = Field(..., description="The actual code snippet")
    dependencies: List[str] = Field(default_factory=list, description="List of internal/external dependencies or imports")
    children: List[MethodNode] = Field(default_factory=list, description="Child method nodes for a class")

class FileNode(BaseModel):
    file_path: str
    imports: List[str] = Field(default_factory=list)
    nodes: List[CodeNode] = Field(default_factory=list)


class IngestionPayload(BaseModel):
    schema_version: str = Field(
        "ingestion-v1",
        description="Schema version for serialized ingestion output",
    )
    root_dir: str = Field(..., description="Absolute repository root used for ingestion")
    files: List[FileNode] = Field(default_factory=list, description="Ingested repository files")
