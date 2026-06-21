"""Minimal Language Server Protocol support for indexed CseGraph repos."""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, BinaryIO, Mapping, Optional, cast
from urllib.parse import unquote, urlparse

from csegraph._core.index.repository import ProjectIndex

LOG = logging.getLogger(__name__)
_EXIT = object()

_SYMBOL_KIND = {
    "file": 1,
    "module": 2,
    "namespace": 3,
    "package": 4,
    "class": 5,
    "method": 6,
    "function": 12,
    "test": 12,
}
_DOCUMENT_SYMBOL_TYPES = ("class", "function", "method", "test")


@dataclass(frozen=True)
class LspServerConfig:
    repo: Path
    db_path: Path


class LspServer:
    """Small JSON-RPC/LSP server backed by the SQLite graph index."""

    def __init__(self, config: LspServerConfig):
        self.config = config
        self._shutdown_requested = False

    def run(self, stdin: BinaryIO, stdout: BinaryIO) -> int:
        while True:
            message = read_message(stdin)
            if message is None:
                return 0
            response = self.handle_message(message)
            if response is _EXIT:
                return 0 if self._shutdown_requested else 1
            if response is not None:
                write_message(stdout, cast(Mapping[str, Any], response))

    def handle_message(self, message: Mapping[str, Any]) -> Optional[dict[str, Any]] | object:
        method = message.get("method")
        message_id = message.get("id")
        is_request = "id" in message

        if method == "exit":
            return _EXIT
        if method == "initialize":
            return _success(message_id, _initialize_result())
        if method == "initialized":
            return None
        if method == "shutdown":
            self._shutdown_requested = True
            return _success(message_id, None)
        if method == "textDocument/documentSymbol":
            return _success(message_id, self._document_symbols(message.get("params")))
        if not is_request:
            return None
        return _error(message_id, -32601, f"Method not found: {method}")

    def _document_symbols(self, params: Any) -> list[dict[str, Any]]:
        rel_path = _document_rel_path(params, self.config.repo)
        if rel_path is None:
            return []

        try:
            index = ProjectIndex(self.config.db_path)
            try:
                rows = index.conn.execute(
                    """
                    SELECT name, type, start_line, end_line
                    FROM nodes
                    WHERE path = ?
                      AND type IN (?, ?, ?, ?)
                    ORDER BY COALESCE(start_line, 0), COALESCE(end_line, 0), name
                    """,
                    (rel_path, *_DOCUMENT_SYMBOL_TYPES),
                ).fetchall()
            finally:
                index.close()
        except sqlite3.Error as exc:
            LOG.debug("Unable to load LSP document symbols for %s: %s", rel_path, exc)
            return []

        return [
            _document_symbol(
                name=row["name"],
                kind=row["type"],
                start_line=row["start_line"],
                end_line=row["end_line"],
            )
            for row in rows
        ]


def run_stdio_lsp(repo: str | Path, db_path: str | Path) -> int:
    config = LspServerConfig(repo=Path(repo).resolve(), db_path=Path(db_path).resolve())
    return LspServer(config).run(sys.stdin.buffer, sys.stdout.buffer)


def read_message(stream: BinaryIO) -> Optional[dict[str, Any]]:
    content_length: Optional[int] = None
    while True:
        line = stream.readline()
        if line == b"":
            return None
        if line in {b"\r\n", b"\n"}:
            break
        name, _, value = line.decode("ascii", errors="replace").partition(":")
        if name.lower() == "content-length":
            content_length = int(value.strip())

    if content_length is None:
        return None
    body = stream.read(content_length)
    if len(body) != content_length:
        return None
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        return None
    return payload


def write_message(stream: BinaryIO, message: Mapping[str, Any]) -> None:
    body = json.dumps(message, separators=(",", ":"), sort_keys=True).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    stream.write(header + body)
    stream.flush()


def _initialize_result() -> dict[str, Any]:
    return {
        "capabilities": {
            "documentSymbolProvider": True,
            "positionEncoding": "utf-16",
            "textDocumentSync": 0,
        },
        "serverInfo": {
            "name": "csegraph-lsp",
            "version": _package_version(),
        },
    }


def _document_rel_path(params: Any, repo: Path) -> Optional[str]:
    if not isinstance(params, dict):
        return None
    text_document = params.get("textDocument")
    if not isinstance(text_document, dict):
        return None
    uri = text_document.get("uri")
    if not isinstance(uri, str):
        return None

    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    candidate = Path(unquote(parsed.path)).resolve()
    try:
        rel_path = candidate.relative_to(repo)
    except ValueError:
        return None
    return rel_path.as_posix()


def _document_symbol(
    *,
    name: str,
    kind: str,
    start_line: Optional[int],
    end_line: Optional[int],
) -> dict[str, Any]:
    range_ = _range(start_line, end_line)
    return {
        "name": name,
        "kind": _SYMBOL_KIND.get(kind, 12),
        "detail": kind,
        "range": range_,
        "selectionRange": range_,
        "children": [],
    }


def _range(start_line: Optional[int], end_line: Optional[int]) -> dict[str, Any]:
    start = max((start_line or 1) - 1, 0)
    end = max(end_line or start_line or 1, start + 1)
    return {
        "start": {"line": start, "character": 0},
        "end": {"line": end, "character": 0},
    }


def _success(message_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _error(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "error": {"code": code, "message": message},
    }


def _package_version() -> str:
    try:
        return version("csegraph")
    except PackageNotFoundError:
        return "0.0.0"
