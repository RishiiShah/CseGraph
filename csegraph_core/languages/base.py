"""Parser and Tokenizer protocols for the language registry."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional, Protocol, runtime_checkable

from csegraph_core.languages.types import ParsedFile


@runtime_checkable
class Parser(Protocol):
    language: str
    extensions: tuple

    def parse(self, path: Path, root: Path) -> ParsedFile: ...
    def iter_files(self, root: Path) -> Iterable[Path]: ...
    def module_name_from_relpath(self, rel_path: str) -> Optional[str]: ...
    def resolve_local_import(
        self,
        import_name: str,
        module_to_file_id: Dict[str, str],
        current_module: Optional[str],
    ) -> Optional[str]: ...


@runtime_checkable
class Tokenizer(Protocol):
    def tokenize(self, text: str) -> List[str]: ...
