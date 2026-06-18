"""csegraph watch — file watcher that auto-refreshes the index on changes."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

from csegraph._core.discovery import is_discoverable_rel_path
from csegraph._core.graph.queries import clear_hub_cache
from csegraph._core.ignore import load_ignore_filter
from csegraph._core.index.services import RefreshService


logger = logging.getLogger(__name__)


def watch(
    repo: str,
    db_path: str,
    profile: str = "medium",
    debounce_ms: int = 500,
    extensions: Optional[set[str]] = None,
) -> None:
    try:
        from watchfiles import watch as _watch, Change
    except ImportError:
        logger.error(
            "csegraph watch requires the 'watchfiles' package.\n"
            "Install or reinstall csegraph with its runtime dependencies."
        )
        sys.exit(1)

    repo_path = Path(repo).resolve()
    if extensions is None:
        from csegraph._core.languages.registry import registry
        extensions = set(registry.supported_extensions())

    def _should_watch(_change: Change, path: str) -> bool:
        p = Path(path)
        if ".csegraph" in p.parts:
            return False
        if p.suffix not in extensions:
            return False
        return True

    logger.info("Watching %s for changes (profile=%s)...", repo_path, profile)

    refresh_svc = RefreshService(db_path)
    try:
        for changes in _watch(str(repo_path), watch_filter=_should_watch, debounce=debounce_ms):
            ignore = load_ignore_filter(repo_path)
            changed_paths = []
            rel_paths = []
            for _, path in changes:
                try:
                    rel = Path(path).resolve().relative_to(repo_path).as_posix()
                except ValueError:
                    continue
                if not is_discoverable_rel_path(rel, ignore):
                    continue
                changed_paths.append(path)
                rel_paths.append(rel)
            if not changed_paths:
                continue

            logger.info(
                "%s file(s) changed: %s%s",
                len(changed_paths),
                ", ".join(rel_paths[:5]),
                "..." if len(rel_paths) > 5 else "",
            )

            try:
                result = refresh_svc.refresh(profile=profile, changed_paths=changed_paths)
                clear_hub_cache()
                logger.info(
                    "Refreshed: %s files, %s symbols, %s edges",
                    result.files_indexed,
                    result.symbols_indexed,
                    result.edges_indexed,
                )
            except Exception as exc:
                logger.error("Refresh error: %s", exc, exc_info=logger.isEnabledFor(logging.DEBUG))
    except KeyboardInterrupt:
        logger.info("Stopped watching.")
