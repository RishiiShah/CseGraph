"""csegraph registry — user-level multi-repo registry at ~/.csegraph/registry.json."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from csegraph._core.core.models import RegistryEntry, RegistryResult

REGISTRY_DIR = Path(os.path.expanduser("~")) / ".csegraph"
REGISTRY_FILE = REGISTRY_DIR / "registry.json"

_ALIAS_RE = re.compile(r"^[A-Za-z0-9_\-.]+$")


class RegistryService:
    def __init__(self, registry_path: Optional[str | Path] = None):
        self.path = Path(registry_path) if registry_path else REGISTRY_FILE

    def _load(self) -> dict:
        if not self.path.exists():
            return {"repos": {}}
        text = self.path.read_text(encoding="utf-8")
        data = json.loads(text)
        if "repos" not in data:
            data["repos"] = {}
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def register(
        self,
        root: str | Path,
        alias: Optional[str] = None,
        profile: str = "medium",
        db: Optional[str | Path] = None,
    ) -> RegistryResult:
        root_path = Path(root).resolve()
        if not root_path.is_dir():
            raise ValueError(f"Repository root does not exist: {root_path}")

        if alias is None:
            alias = root_path.name

        if not alias or ".." in alias or not _ALIAS_RE.match(alias):
            raise ValueError(f"Invalid alias {alias!r}: must be alphanumeric with _ - .")

        if db is None:
            db_path = str(root_path / ".csegraph" / "index.db")
        else:
            db_path = str(Path(db).resolve())

        data = self._load()
        now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        existing = data["repos"].get(alias)
        action = "updated" if existing else "registered"

        data["repos"][alias] = {
            "root": str(root_path),
            "db": db_path,
            "profile": profile,
            "added_at": existing["added_at"] if existing else now,
        }
        self._save(data)

        entry = RegistryEntry(
            alias=alias,
            root=str(root_path),
            db=db_path,
            profile=profile,
            added_at=data["repos"][alias]["added_at"],
        )
        return RegistryResult(
            command="registry",
            entries=[entry],
            action=action,
            alias=alias,
            message=f"{action.capitalize()} '{alias}' -> {root_path}",
        )

    def unregister(self, alias: str) -> RegistryResult:
        data = self._load()
        if alias not in data["repos"]:
            raise ValueError(f"No registered repo with alias '{alias}'")

        removed = data["repos"].pop(alias)
        self._save(data)

        entry = RegistryEntry(
            alias=alias,
            root=removed["root"],
            db=removed["db"],
            profile=removed.get("profile", "medium"),
            added_at=removed.get("added_at"),
        )
        return RegistryResult(
            command="registry",
            entries=[entry],
            action="unregistered",
            alias=alias,
            message=f"Unregistered '{alias}'",
        )

    def list(self) -> RegistryResult:
        data = self._load()
        entries = []
        for alias, info in sorted(data["repos"].items()):
            entries.append(
                RegistryEntry(
                    alias=alias,
                    root=info["root"],
                    db=info["db"],
                    profile=info.get("profile", "medium"),
                    added_at=info.get("added_at"),
                )
            )
        return RegistryResult(
            command="registry",
            entries=entries,
            action="list",
            message=f"{len(entries)} registered repo(s)",
        )

    def get(self, alias: str) -> RegistryEntry:
        data = self._load()
        if alias not in data["repos"]:
            raise ValueError(f"No registered repo with alias '{alias}'")
        info = data["repos"][alias]
        return RegistryEntry(
            alias=alias,
            root=info["root"],
            db=info["db"],
            profile=info.get("profile", "medium"),
            added_at=info.get("added_at"),
        )

    def status(self, alias: str) -> RegistryResult:
        from csegraph._core.status import StatusService

        entry = self.get(alias)
        warnings = []
        if not Path(entry.root).is_dir():
            warnings.append(f"Root directory missing: {entry.root}")
        if not Path(entry.db).exists():
            warnings.append(f"Index not built: {entry.db}")

        message_parts = [f"'{alias}' -> {entry.root}"]
        if warnings:
            message_parts.extend(warnings)
        else:
            try:
                sr = StatusService(entry.db).status()
                message_parts.append(
                    f"{sr.total_nodes} nodes, {sr.total_edges} edges, {sr.total_files} files"
                )
                if sr.warnings:
                    message_parts.extend(sr.warnings)
            except Exception as exc:
                message_parts.append(f"Status error: {exc}")

        return RegistryResult(
            command="registry",
            entries=[entry],
            action="status",
            alias=alias,
            message=" | ".join(message_parts),
        )
