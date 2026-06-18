from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from csegraph._core.index.services import IndexService
from csegraph._core.lsp import LspServer, LspServerConfig


def _frame(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return b"Content-Length: %d\r\n\r\n" % len(body) + body


def _messages(data: bytes) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    stream = io.BytesIO(data)
    while True:
        header = stream.readline()
        if header == b"":
            break
        assert header.startswith(b"Content-Length: ")
        length = int(header.partition(b": ")[2])
        assert stream.readline() == b"\r\n"
        messages.append(json.loads(stream.read(length)))
    return messages


def _index(sample_repo: Path) -> Path:
    db_path = sample_repo / ".csegraph" / "index.db"
    IndexService(db_path).index(sample_repo)
    return db_path


def test_lsp_initialize_and_document_symbols(sample_repo: Path) -> None:
    db_path = _index(sample_repo)
    stdin = io.BytesIO(
        _frame({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        + _frame(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "textDocument/documentSymbol",
                "params": {
                    "textDocument": {"uri": (sample_repo / "service.py").as_uri()}
                },
            }
        )
        + _frame({"jsonrpc": "2.0", "id": 3, "method": "shutdown"})
        + _frame({"jsonrpc": "2.0", "method": "exit"})
    )
    stdout = io.BytesIO()

    exit_code = LspServer(
        LspServerConfig(repo=sample_repo.resolve(), db_path=db_path.resolve())
    ).run(stdin, stdout)

    assert exit_code == 0
    messages = _messages(stdout.getvalue())
    assert messages[0]["result"]["capabilities"]["documentSymbolProvider"] is True
    symbols = messages[1]["result"]
    create_user = next(symbol for symbol in symbols if symbol["name"] == "create_user")
    assert create_user["kind"] == 12
    assert create_user["detail"] == "function"
    assert create_user["range"]["start"] == {"line": 2, "character": 0}
    assert messages[2]["result"] is None


def test_lsp_cli_stdio_smoke(sample_repo: Path) -> None:
    _index(sample_repo)
    proc = subprocess.run(
        [sys.executable, "-m", "csegraph._cli", "lsp", str(sample_repo)],
        input=(
            _frame({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            + _frame({"jsonrpc": "2.0", "id": 2, "method": "shutdown"})
            + _frame({"jsonrpc": "2.0", "method": "exit"})
        ),
        check=True,
        capture_output=True,
    )

    messages = _messages(proc.stdout)
    assert messages[0]["result"]["serverInfo"]["name"] == "csegraph-lsp"
    assert messages[1]["result"] is None
