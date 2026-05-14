from __future__ import annotations

from pathlib import Path
from typing import List

from csegraph_core.core.models import PostprocessResult
from csegraph_core.index.repository import ProjectIndex
from csegraph_core.languages.registry import UnsupportedLanguageError, registry


class PostprocessService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def postprocess(
        self,
        *,
        no_fts: bool = False,
        no_communities: bool = False,
    ) -> PostprocessResult:
        # Preflight check: ensure DB exists and has been indexed
        if not Path(self.db_path).exists():
            raise ValueError("No csegraph index found. Run csegraph index . first.")

        index = ProjectIndex(self.db_path)
        try:
            try:
                meta = index.metadata(raise_if_empty=True)
            except ValueError:
                raise ValueError(
                    "No csegraph index found. Run csegraph index . first."
                )
            repo_root = meta["root_dir"]

            skipped: List[str] = []
            fts_entries = 0
            communities_detected = 0
            modularity = 0.0

            if no_fts:
                skipped.append("fts")
            else:
                fts_entries = _rebuild_fts(index, repo_root)

            if no_communities:
                skipped.append("communities")
            else:
                from csegraph_core.graph.communities import detect_communities
                result = detect_communities(self.db_path)
                communities_detected = result.num_communities
                modularity = result.modularity

            return PostprocessResult(
                command="postprocess",
                db_path=self.db_path,
                repo_root=repo_root,
                fts_entries=fts_entries,
                communities_detected=communities_detected,
                modularity=modularity,
                skipped=skipped,
            )
        finally:
            index.close()


def _rebuild_fts(index: ProjectIndex, repo_root: str) -> int:
    index.conn.execute("DELETE FROM lexical_index")

    rows = index.conn.execute(
        """
        SELECT
            n.id, n.type, n.name, n.path, n.language,
            n.signature, n.docstring, n.start_line, n.end_line,
            COALESCE(s.summary, '') AS summary
        FROM nodes n
        LEFT JOIN summaries s ON s.node_id = n.id
        WHERE n.type IN ('file', 'class', 'function', 'method', 'test')
        """
    ).fetchall()

    batch = []
    for row in rows:
        source = ""
        if row["type"] != "file" and row["start_line"] and row["end_line"]:
            source = _read_source_slice(
                repo_root, row["path"], row["language"],
                row["start_line"], row["end_line"],
            )

        batch.append((
            row["id"],
            row["name"],
            row["path"],
            row["signature"] or "",
            row["docstring"] or "",
            row["summary"],
            source,
        ))

    if batch:
        index.conn.executemany(
            """
            INSERT INTO lexical_index(node_id, name, path, signature, docstring, summary, source)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            batch,
        )
    index.conn.commit()
    return len(batch)


def _read_source_slice(
    repo_root: str,
    rel_path: str,
    language: str,
    start_line: int,
    end_line: int,
) -> str:
    try:
        full_path = Path(repo_root) / rel_path
        lines = full_path.read_text(errors="replace").splitlines()
        source = "\n".join(lines[start_line - 1 : end_line])
    except (OSError, IndexError):
        return ""

    try:
        tokenizer = registry.tokenizer_for(language)
        return " ".join(tokenizer.tokenize(source))
    except (UnsupportedLanguageError, Exception):
        return ""
