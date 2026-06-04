from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List

from csegraph._core.core.models import PostprocessResult, to_dict
from csegraph._core.index.repository import ProjectIndex
from csegraph._core.languages.registry import UnsupportedLanguageError, registry


POSTPROCESS_LEVELS = ("none", "minimal", "full")


class PostprocessService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def postprocess(
        self,
        *,
        level: str = "full",
        no_fts: bool = False,
        no_communities: bool = False,
    ) -> PostprocessResult:
        if level not in POSTPROCESS_LEVELS:
            raise ValueError(f"level must be one of {POSTPROCESS_LEVELS}, got '{level}'")

        if not Path(self.db_path).exists():
            raise ValueError("No csegraph index found. Run csegraph index first.")

        if level == "none":
            index = ProjectIndex(self.db_path)
            try:
                try:
                    meta = index.metadata(raise_if_empty=True)
                except ValueError:
                    raise ValueError(
                        "No csegraph index found. Run csegraph index first."
                    )
                return PostprocessResult(
                    command="postprocess",
                    db_path=self.db_path,
                    repo_root=meta["root_dir"],
                    fts_entries=0,
                    communities_detected=0,
                    skipped=["fts", "resolvers", "communities"],
                    level="none",
                )
            finally:
                index.close()

        index = ProjectIndex(self.db_path)
        try:
            try:
                meta = index.metadata(raise_if_empty=True)
            except ValueError:
                raise ValueError(
                    "No csegraph index found. Run csegraph index first."
                )
            repo_root = meta["root_dir"]

            timings: Dict[str, float] = {}
            skipped: List[str] = []
            fts_entries = 0
            communities_detected = 0
            modularity = 0.0

            skip_fts = no_fts or level == "none"
            skip_communities = no_communities or level in ("none", "minimal")

            if skip_fts:
                skipped.append("fts")
            else:
                start = time.perf_counter()
                fts_entries = _rebuild_fts(index, repo_root)
                timings["fts_rebuild_ms"] = _elapsed_ms(start)

            resolvers_edges_added = 0
            skip_resolvers = level in ("none", "minimal")
            if skip_resolvers:
                skipped.append("resolvers")
            else:
                start = time.perf_counter()
                from csegraph._core.graph.resolvers import ResolverService
                resolver_result = ResolverService(self.db_path).run_all()
                resolvers_edges_added = resolver_result.total_edges_added
                timings["resolvers_ms"] = _elapsed_ms(start)

            if skip_communities:
                skipped.append("communities")
            else:
                start = time.perf_counter()
                from csegraph._core.graph.communities import detect_communities
                result = detect_communities(self.db_path)
                communities_detected = result.num_communities
                modularity = result.modularity
                timings["community_detection_ms"] = _elapsed_ms(start)

            return PostprocessResult(
                command="postprocess",
                db_path=self.db_path,
                repo_root=repo_root,
                fts_entries=fts_entries,
                communities_detected=communities_detected,
                modularity=modularity,
                resolvers_edges_added=resolvers_edges_added,
                skipped=skipped,
                level=level,
                timings_ms=timings,
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
        root = Path(repo_root).resolve()
        full_path = (root / rel_path).resolve()
        if not full_path.is_relative_to(root):
            return ""
        lines = full_path.read_text(errors="replace").splitlines()
        source = "\n".join(lines[start_line - 1 : end_line])
    except (OSError, IndexError):
        return ""

    try:
        tokenizer = registry.tokenizer_for(language)
        return " ".join(tokenizer.tokenize(source))
    except UnsupportedLanguageError:
        return ""


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)


def attach_postprocess_metadata(
    result: Any,
    db: str,
    level: str,
    postprocess_result: Any | None,
    skipped_reason: str | None,
) -> None:
    """Attach postprocess level/stats and best-effort graph totals to index/refresh results."""
    result.postprocess_level = level
    if postprocess_result is not None:
        result.postprocess = to_dict(postprocess_result)
    result.postprocess_skipped_reason = skipped_reason
    try:
        from csegraph._core.status import StatusService

        status = StatusService(db).status()
        result.graph_totals = {
            "files": status.total_files,
            "nodes": status.total_nodes,
            "edges": status.total_edges,
        }
    except Exception:
        result.graph_totals = {}
