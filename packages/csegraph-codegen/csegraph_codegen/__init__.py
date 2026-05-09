"""csegraph-codegen v1.3.0.

Optional LLM-powered code generation add-on for csegraph. The core engine,
SDK facade, and CLI do not depend on this package.
"""

from csegraph_codegen.models import CodegenResult
from csegraph_codegen.service import CodegenService

__version__ = "1.3.0"

__all__ = ["__version__", "CodegenResult", "CodegenService"]
