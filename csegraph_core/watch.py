"""csegraph watch — file watcher that auto-refreshes the index on changes."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional

from csegraph_core.index.services import RefreshService


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
        print(
            "csegraph watch requires the 'watchfiles' package.\n"
            "Install it with: pip install csegraph-core[watch]",
            file=sys.stderr,
        )
        sys.exit(1)

    repo_path = Path(repo).resolve()
    if extensions is None:
        from csegraph_core.languages.registry import registry
        extensions = set(registry.supported_extensions())

    def _should_watch(change: Change, path: str) -> bool:
        p = Path(path)
        if ".csegraph" in p.parts:
            return False
        if p.suffix not in extensions:
            return False
        return True

    print(f"Watching {repo_path} for changes (profile={profile})...", file=sys.stderr, flush=True)

    refresh_svc = RefreshService(db_path)
    for changes in _watch(str(repo_path), watch_filter=_should_watch, debounce=debounce_ms):
        changed_paths = [path for _, path in changes]
        rel_paths = []
        for p in changed_paths:
            try:
                rel_paths.append(str(Path(p).relative_to(repo_path)))
            except ValueError:
                rel_paths.append(p)

        print(
            f"[{time.strftime('%H:%M:%S')}] {len(changed_paths)} file(s) changed: "
            f"{', '.join(rel_paths[:5])}{'...' if len(rel_paths) > 5 else ''}",
            file=sys.stderr,
            flush=True,
        )

        try:
            result = refresh_svc.refresh(profile=profile)
            print(
                f"[{time.strftime('%H:%M:%S')}] Refreshed: "
                f"{result.files_indexed} files, {result.symbols_indexed} symbols, "
                f"{result.edges_indexed} edges",
                file=sys.stderr,
                flush=True,
            )
        except Exception as exc:
            print(f"[{time.strftime('%H:%M:%S')}] Refresh error: {exc}", file=sys.stderr, flush=True)
