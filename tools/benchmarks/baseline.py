"""Compatibility adapter for the canonical tool-using repository agent.

The agent implementation lives in :mod:`tools.benchmarks.agent`; this module
keeps the benchmark's public adapter and optional Pyright provider stable.
"""

from __future__ import annotations

import json
import os
import re
import select
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Protocol, Sequence
from urllib.parse import unquote, urlparse

from tools.benchmarks.agent import RepositoryAgent, RepositoryAgentProfile, profile_for_repository
from tools.benchmarks.models import (
    BaselineResult,
)

PINNED_PYRIGHT_VERSION = "1.1.407"
LOCAL_COPY_URLS = frozenset({"fixture://local", "sandbox://local"})


class DefinitionProvider(Protocol):
    def definitions(
        self,
        repo: Path,
        path: Path,
        line: int,
        character: int,
    ) -> Sequence[tuple[Path, int]]:
        """Return definition locations using a pinned external LSP."""

    def references(
        self,
        repo: Path,
        path: Path,
        line: int,
        character: int,
    ) -> Sequence[tuple[Path, int]]:
        """Return reference locations using a pinned external LSP."""


class PyrightLspProvider:
    """Small persistent JSON-RPC client for a pinned Pyright language server.

    Pyright is deliberately optional. A missing binary, a version mismatch, or
    a protocol failure disables LSP enrichment and leaves the rg baseline
    usable. The benchmark report exposes the reason instead of silently
    claiming that LSP participated.
    """

    def __init__(
        self,
        *,
        command: Sequence[str] = ("pyright-langserver", "--stdio"),
        version_command: Sequence[str] = ("pyright", "--version"),
        expected_version: str = PINNED_PYRIGHT_VERSION,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.command = tuple(command)
        self.version_command = tuple(version_command)
        self.expected_version = expected_version
        self.timeout_seconds = timeout_seconds
        self.warning: str | None = None
        self.observed_version: str | None = None
        self.last_latency_ms = 0.0
        self._process: subprocess.Popen[bytes] | None = None
        self._buffer = bytearray()
        self._request_id = 0
        self._root: Path | None = None
        self._opened: set[Path] = set()
        self._available = self._check_available()

    @property
    def available(self) -> bool:
        return self._available

    def _check_available(self) -> bool:
        if not self.command or shutil.which(self.command[0]) is None:
            self.warning = f"Pyright LSP unavailable: {self.command[0]!r} was not found"
            return False
        if not self.version_command or shutil.which(self.version_command[0]) is None:
            self.warning = (
                "Pyright LSP disabled: the version probe command "
                f"{self.version_command[0]!r} was not found"
            )
            return False
        try:
            result = subprocess.run(
                self.version_command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.warning = f"Pyright LSP disabled: version probe failed ({exc})"
            return False
        version_text = f"{result.stdout}\n{result.stderr}"
        match = re.search(r"(\d+\.\d+\.\d+)", version_text)
        observed = match.group(1) if match else None
        self.observed_version = observed
        if result.returncode != 0 or observed != self.expected_version:
            self.warning = (
                "Pyright LSP disabled: expected version "
                f"{self.expected_version}, observed {observed or 'unknown'}"
            )
            return False
        return True

    def definitions(
        self,
        repo: Path,
        path: Path,
        line: int,
        character: int,
    ) -> Sequence[tuple[Path, int]]:
        return self._locations(
            repo,
            path,
            line,
            character,
            method="textDocument/definition",
        )

    def references(
        self,
        repo: Path,
        path: Path,
        line: int,
        character: int,
    ) -> Sequence[tuple[Path, int]]:
        return self._locations(
            repo,
            path,
            line,
            character,
            method="textDocument/references",
            extra={"context": {"includeDeclaration": False}},
        )

    def _locations(
        self,
        repo: Path,
        path: Path,
        line: int,
        character: int,
        *,
        method: str,
        extra: dict[str, Any] | None = None,
    ) -> list[tuple[Path, int]]:
        if not self._available:
            return []
        started = time.perf_counter()
        try:
            self._ensure_started(repo)
            self._open_document(path)
            params: dict[str, Any] = {
                "textDocument": {"uri": path.resolve().as_uri()},
                "position": {
                    "line": max(0, line - 1),
                    "character": max(0, character),
                },
            }
            if extra:
                params.update(extra)
            response = self._request(method, params)
            return _lsp_locations(response.get("result"))
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            self.warning = f"Pyright LSP disabled after protocol failure: {exc}"
            self._available = False
            self.close()
            return []
        finally:
            self.last_latency_ms = round((time.perf_counter() - started) * 1000, 3)

    def _ensure_started(self, repo: Path) -> None:
        repo = repo.resolve()
        if self._process is not None and self._root == repo:
            return
        self.close()
        self._process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        self._root = repo
        self._opened.clear()
        self._buffer.clear()
        self._request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": repo.as_uri(),
                "capabilities": {},
                "workspaceFolders": [{"uri": repo.as_uri(), "name": repo.name}],
            },
        )
        self._notify("initialized", {})

    def _open_document(self, path: Path) -> None:
        resolved = path.resolve()
        if resolved in self._opened:
            return
        text = resolved.read_text(encoding="utf-8", errors="replace")
        self._notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": resolved.as_uri(),
                    "languageId": "python",
                    "version": 1,
                    "text": text,
                }
            },
        )
        self._opened.add(resolved)

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._request_id += 1
        request_id = self._request_id
        self._write(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            message = self._read(deadline)
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(f"{method} failed: {message['error']}")
                return message

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _write(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise RuntimeError("Pyright language server is not running")
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        process.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
        process.stdin.flush()

    def _read(self, deadline: float) -> dict[str, Any]:
        process = self._process
        if process is None or process.stdout is None:
            raise RuntimeError("Pyright language server is not running")
        while True:
            separator = self._buffer.find(b"\r\n\r\n")
            if separator >= 0:
                header = bytes(self._buffer[:separator]).decode("ascii", errors="replace")
                match = re.search(r"(?im)^Content-Length:\s*(\d+)\s*$", header)
                if match is None:
                    raise RuntimeError("Pyright returned an invalid JSON-RPC header")
                length = int(match.group(1))
                body_start = separator + 4
                body_end = body_start + length
                if len(self._buffer) >= body_end:
                    body = bytes(self._buffer[body_start:body_end])
                    del self._buffer[:body_end]
                    value = json.loads(body)
                    if not isinstance(value, dict):
                        raise RuntimeError("Pyright returned a non-object JSON-RPC message")
                    return value
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("timed out waiting for Pyright")
            ready, _, _ = select.select([process.stdout.fileno()], [], [], remaining)
            if not ready:
                raise RuntimeError("timed out waiting for Pyright")
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError("Pyright exited before returning a response")
            self._buffer.extend(chunk)

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        self._root = None
        self._opened.clear()
        self._buffer.clear()

    def __enter__(self) -> PyrightLspProvider:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class StrongBaselineAdapter:
    def __init__(
        self,
        *,
        encoding: str = "o200k_base",
        definition_provider: DefinitionProvider | None = None,
    ) -> None:
        self.encoding = encoding
        self.definition_provider = definition_provider
        self._agent = RepositoryAgent(encoding=encoding)

    def retrieve(
        self,
        repo: str | Path,
        task: str,
        *,
        target: str | None = None,
        task_kind: str = "auto",
        visible_target: str | None = None,
        token_budget: int = 800,
        temperature: str = "cold",
        profile_key: str | None = None,
        profile: RepositoryAgentProfile | None = None,
    ) -> BaselineResult:
        return self._agent.retrieve(
            repo,
            task,
            task_kind=task_kind,
            visible_target=visible_target,
            profile=profile or profile_for_repository(Path(repo), profile_key),
            token_budget=token_budget,
            temperature=temperature,
            definition_provider=self.definition_provider,
        )


def _lsp_locations(value: Any) -> list[tuple[Path, int]]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    locations: list[tuple[Path, int]] = []
    seen: set[tuple[Path, int]] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        uri = item.get("uri") or item.get("targetUri")
        range_value = item.get("range") or item.get("targetSelectionRange")
        if not isinstance(uri, str) or not isinstance(range_value, dict):
            continue
        start = range_value.get("start")
        if not isinstance(start, dict):
            continue
        parsed = urlparse(uri)
        if parsed.scheme != "file":
            continue
        path = Path(unquote(parsed.path))
        line = int(start.get("line", 0)) + 1
        key = (path, line)
        if key not in seen:
            seen.add(key)
            locations.append(key)
    return locations
