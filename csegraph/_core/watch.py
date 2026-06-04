"""csegraph watch — file watcher that auto-refreshes the index on changes."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional

from csegraph._core.discovery import is_discoverable_rel_path
from csegraph._core.graph.queries import clear_hub_cache
from csegraph._core.ignore import load_ignore_filter
from csegraph._core.index.services import RefreshService


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
            "Install or reinstall csegraph with its runtime dependencies.",
            file=sys.stderr,
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

    print(f"Watching {repo_path} for changes (profile={profile})...", file=sys.stderr, flush=True)

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

            print(
                f"[{time.strftime('%H:%M:%S')}] {len(changed_paths)} file(s) changed: "
                f"{', '.join(rel_paths[:5])}{'...' if len(rel_paths) > 5 else ''}",
                file=sys.stderr,
                flush=True,
            )

            try:
                result = refresh_svc.refresh(profile=profile, changed_paths=changed_paths)
                clear_hub_cache()
                print(
                    f"[{time.strftime('%H:%M:%S')}] Refreshed: "
                    f"{result.files_indexed} files, {result.symbols_indexed} symbols, "
                    f"{result.edges_indexed} edges",
                    file=sys.stderr,
                    flush=True,
                )
            except Exception as exc:
                print(f"[{time.strftime('%H:%M:%S')}] Refresh error: {exc}", file=sys.stderr, flush=True)
    except KeyboardInterrupt:
        print(f"\n[{time.strftime('%H:%M:%S')}] Stopped watching.", file=sys.stderr, flush=True)
