"""csegraph daemon — multi-repo watch manager.

Spawns one watch subprocess per registered repo, tracks PIDs in
~/.csegraph/pids/, and provides start/stop/status lifecycle commands.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from csegraph._core.core.models import DaemonEntry, DaemonResult
from csegraph._core.registry import RegistryService

PIDS_DIR = Path(os.path.expanduser("~")) / ".csegraph" / "pids"

_ALIAS_RE = re.compile(r"^[A-Za-z0-9_\-.]+$")


def _validate_alias(alias: str) -> None:
    if not alias or ".." in alias or not _ALIAS_RE.match(alias):
        raise ValueError(f"Invalid alias {alias!r}: must be alphanumeric with _ - .")


def _pid_file(alias: str) -> Path:
    _validate_alias(alias)
    return PIDS_DIR / f"{alias}.pid"


def _log_file(alias: str, base_dir: Optional[str | Path] = None) -> Path:
    _validate_alias(alias)
    root = Path(base_dir) if base_dir else Path(os.path.expanduser("~")) / ".csegraph"
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"{alias}.log"


def _is_alive(pid: int) -> bool:
    try:
        if sys.platform == "win32":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        else:
            os.kill(pid, 0)
            return True
    except (OSError, ProcessLookupError):
        return False


def _kill_process(pid: int) -> bool:
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True,
                timeout=5,
            )
        else:
            os.kill(pid, signal.SIGTERM)
            for _ in range(10):
                time.sleep(0.1)
                try:
                    os.kill(pid, 0)
                except (OSError, ProcessLookupError):
                    return True
            os.kill(pid, signal.SIGKILL)
        return True
    except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
        return True


class DaemonService:
    def __init__(
        self,
        registry_path: Optional[str | Path] = None,
        pids_dir: Optional[str | Path] = None,
    ):
        self.registry = RegistryService(registry_path)
        self.pids_dir = Path(pids_dir) if pids_dir else PIDS_DIR

    def _read_pid(self, alias: str) -> Optional[int]:
        pf = self.pids_dir / f"{alias}.pid"
        if not pf.exists():
            return None
        try:
            return int(pf.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            return None

    def _write_pid(self, alias: str, pid: int) -> None:
        self.pids_dir.mkdir(parents=True, exist_ok=True)
        (self.pids_dir / f"{alias}.pid").write_text(
            str(pid), encoding="utf-8"
        )

    def _remove_pid(self, alias: str) -> None:
        pf = self.pids_dir / f"{alias}.pid"
        if pf.exists():
            pf.unlink()

    def _entry_status(self, alias: str, root: str) -> DaemonEntry:
        pid = self._read_pid(alias)
        if pid is not None and _is_alive(pid):
            return DaemonEntry(
                alias=alias, root=root, pid=pid, status="running"
            )
        if pid is not None:
            self._remove_pid(alias)
        return DaemonEntry(alias=alias, root=root, status="stopped")

    def start(
        self,
        aliases: Optional[List[str]] = None,
        profile: Optional[str] = None,
    ) -> DaemonResult:
        reg = self.registry.list()
        if not reg.entries:
            return DaemonResult(
                command="daemon",
                running=False,
                message="No repos registered. Run 'csegraph registry register <path>' first.",
            )

        targets = reg.entries
        if aliases:
            alias_set = set(aliases)
            targets = [e for e in targets if e.alias in alias_set]
            missing = alias_set - {e.alias for e in targets}
            if missing:
                raise ValueError(
                    f"Unknown aliases: {sorted(missing)}"
                )

        entries: list[DaemonEntry] = []
        for entry in targets:
            existing_pid = self._read_pid(entry.alias)
            if existing_pid is not None and _is_alive(existing_pid):
                entries.append(DaemonEntry(
                    alias=entry.alias,
                    root=entry.root,
                    pid=existing_pid,
                    status="already_running",
                ))
                continue

            watch_profile = profile or entry.profile
            log_path = _log_file(entry.alias, self.pids_dir.parent)

            try:
                with open(log_path, "a", encoding="utf-8") as log_fh:
                    proc = subprocess.Popen(
                        [
                            sys.executable, "-m", "csegraph._cli",
                            "watch", entry.root,
                            "--db", entry.db,
                            "--profile", watch_profile,
                        ],
                        stdout=log_fh,
                        stderr=log_fh,
                        start_new_session=True,
                    )
                self._write_pid(entry.alias, proc.pid)
                entries.append(DaemonEntry(
                    alias=entry.alias,
                    root=entry.root,
                    pid=proc.pid,
                    status="started",
                ))
            except Exception as exc:
                entries.append(DaemonEntry(
                    alias=entry.alias,
                    root=entry.root,
                    status="error",
                    error=str(exc),
                ))

        running_count = sum(
            1 for e in entries if e.status in ("started", "already_running")
        )
        return DaemonResult(
            command="daemon",
            running=running_count > 0,
            entries=entries,
            pid_file=str(self.pids_dir),
            message=f"{running_count} watcher(s) active",
        )

    def stop(
        self, aliases: Optional[List[str]] = None
    ) -> DaemonResult:
        reg = self.registry.list()
        targets = reg.entries
        if aliases:
            alias_set = set(aliases)
            targets = [e for e in targets if e.alias in alias_set]

        entries: list[DaemonEntry] = []
        for entry in targets:
            pid = self._read_pid(entry.alias)
            if pid is None or not _is_alive(pid):
                self._remove_pid(entry.alias)
                entries.append(DaemonEntry(
                    alias=entry.alias,
                    root=entry.root,
                    status="not_running",
                ))
                continue

            _kill_process(pid)
            self._remove_pid(entry.alias)
            entries.append(DaemonEntry(
                alias=entry.alias,
                root=entry.root,
                pid=pid,
                status="stopped",
            ))

        return DaemonResult(
            command="daemon",
            running=False,
            entries=entries,
            pid_file=str(self.pids_dir),
            message=f"Stopped {sum(1 for e in entries if e.status == 'stopped')} watcher(s)",
        )

    def status(self) -> DaemonResult:
        reg = self.registry.list()
        entries: list[DaemonEntry] = []
        for entry in reg.entries:
            entries.append(self._entry_status(entry.alias, entry.root))

        running_count = sum(1 for e in entries if e.status == "running")
        return DaemonResult(
            command="daemon",
            running=running_count > 0,
            entries=entries,
            pid_file=str(self.pids_dir),
            message=f"{running_count}/{len(entries)} watcher(s) running",
        )
