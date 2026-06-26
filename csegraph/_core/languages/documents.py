from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from csegraph._core.core.ids import symbol_node_id
from csegraph._core.languages.base import BaseParser, sha256_text, to_repo_relative
from csegraph._core.languages.types import ParsedFile, ParsedSymbol


class DocumentParser(BaseParser):
    language = "document"
    extensions = (".md", ".markdown", ".rst", ".txt", ".adoc")

    def parse(self, path: Path, root_dir: Path) -> ParsedFile:
        source = path.read_text(encoding="utf-8")
        rel_path = to_repo_relative(path, root_dir)
        lines = source.splitlines()
        title = _document_title(path, lines)
        source_hash = sha256_text(source)
        symbol = ParsedSymbol(
            node_id=symbol_node_id(rel_path, "document", title),
            kind="document",
            name=title,
            file_path=rel_path,
            start_line=1,
            end_line=max(1, len(lines)),
            signature=f"document {title}",
            docstring=_first_paragraph(lines),
            source=source,
            source_hash=source_hash,
        )
        stat = path.stat()
        return ParsedFile(
            rel_path=rel_path,
            abs_path=str(path.resolve()),
            sha256=source_hash,
            mtime=stat.st_mtime,
            size=stat.st_size,
            language=self.language,
            symbols=[symbol],
        )

    def module_name_from_relpath(self, rel_path: str) -> Optional[str]:
        return None

    def resolve_local_import(
        self,
        import_name: str,
        module_to_file_id: Dict[str, str],
        current_module: Optional[str],
    ) -> Optional[str]:
        return None


def _document_title(path: Path, lines: list[str]) -> str:
    for line in lines:
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:120]
    return path.stem


def _first_paragraph(lines: list[str]) -> str:
    paragraph: list[str] = []
    for line in lines:
        stripped = line.strip().lstrip("#").strip()
        if not stripped:
            if paragraph:
                break
            continue
        paragraph.append(stripped)
    return " ".join(paragraph)[:500]
