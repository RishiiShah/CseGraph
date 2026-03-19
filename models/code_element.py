from pydantic import BaseModel, Field
from typing import List, Optional, Any

class CodeNode(BaseModel):
    name: str = Field(..., description="Name of the node (e.g., function name, class name)")
    node_type: str = Field(..., description="Type of the node: 'function', 'class', 'method', 'file'")
    file_path: str = Field(..., description="Path to the file containing this node")
    start_line: int = Field(..., description="Starting line number")
    end_line: int = Field(..., description="Ending line number")
    docstring: Optional[str] = Field(None, description="Docstring associated with the node")
    code_content: str = Field(..., description="The actual code snippet")
    dependencies: List[str] = Field(default_factory=list, description="List of internal/external dependencies or imports")
    children: List[Any] = Field(default_factory=list, description="Child nodes (e.g., methods of a class)")

class FileNode(BaseModel):
    file_path: str
    imports: List[str] = Field(default_factory=list)
    nodes: List[CodeNode] = Field(default_factory=list)
