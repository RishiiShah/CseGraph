from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from csegraph._core.core.models import StatusResult
from csegraph._core.index.repository import ProjectIndex
from csegraph._core.index.schema import SCHEMA_VERSION
from csegraph._core.repo_state import git_head_state


class StatusService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def status(self) -> StatusResult:
        if not Path(self.db_path).exists():
            raise ValueError("No csegraph index found. Run csegraph index first.")

        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            meta = index.metadata()
            conn = index.conn

            repo_root = meta["root_dir"]
            total_files = conn.execute("SELECT count(*) FROM files").fetchone()[0]
            total_symbols = conn.execute("SELECT count(*) FROM symbols").fetchone()[0]
            total_nodes = total_files + total_symbols
            total_edges = conn.execute("SELECT count(*) FROM edges").fetchone()[0]

            languages = sorted(
                row[0]
                for row in conn.execute("SELECT DISTINCT language FROM files ORDER BY language")
            )

            parse_error_count = conn.execute(
                "SELECT count(*) FROM files WHERE parse_status = 'error'"
            ).fetchone()[0]

            current_branch, current_commit = (
                git_head_state(repo_root) if Path(repo_root).exists() else (None, None)
            )
            warnings = _build_warnings(meta, repo_root, current_branch, current_commit)

            built_branch = meta.get("built_branch") or None
            built_commit = meta.get("built_commit") or None

            return StatusResult(
                schema_version=meta.get("schema_version", ""),
                total_nodes=total_nodes,
                total_edges=total_edges,
                total_files=total_files,
                languages=languages,
                parse_error_count=parse_error_count,
                created_at=_epoch_to_iso(meta.get("created_at")),
                updated_at=_epoch_to_iso(meta.get("updated_at")),
                built_branch=built_branch,
                built_commit=built_commit,
                current_branch=current_branch,
                current_commit=current_commit,
                warnings=warnings,
            )
        finally:
            index.close()


def _epoch_to_iso(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    except (ValueError, OSError):
        return None


def _build_warnings(
    meta: Dict[str, str],
    repo_root: str,
    current_branch: Optional[str] = None,
    current_commit: Optional[str] = None,
) -> List[str]:
    warnings: List[str] = []
    schema = meta.get("schema_version")
    if schema and schema != SCHEMA_VERSION:
        warnings.append(
            f"Schema mismatch: index has '{schema}' but current version is '{SCHEMA_VERSION}'. "
            "Run 'csegraph index' to rebuild."
        )

    built_branch = meta.get("built_branch")
    built_commit = meta.get("built_commit")

    if built_branch and current_branch and built_branch != current_branch:
        warnings.append(
            f"Graph was built on '{built_branch}' but you are now on '{current_branch}'. "
            "Run 'csegraph index' to rebuild."
        )
    if built_commit and current_commit and built_commit != current_commit:
        warnings.append(
            f"Graph was built at commit {built_commit} but HEAD is now {current_commit}. "
            "Run 'csegraph refresh' to update."
        )

    return warnings
