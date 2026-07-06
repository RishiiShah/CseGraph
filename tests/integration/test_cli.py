from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "csegraph._cli", *args],
        check=True,
        capture_output=True,
        text=True,
    )


def test_cli_index_context_json_and_markdown(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")

    indexed = json.loads(_run("index", str(repo), "--json").stdout)
    context = json.loads(
        _run(
            "context",
            "Explain run",
            "--repo",
            str(repo),
            "--target",
            "run",
        ).stdout
    )
    markdown = _run(
        "context",
        "Explain run",
        "--repo",
        str(repo),
        "--target",
        "run",
        "--format",
        "markdown",
    ).stdout

    assert indexed["files_indexed"] == 1
    assert set(context) == {"schema_version", "status", "slices"}
    assert "app.py" in markdown
    assert "def run" in markdown


def test_removed_command_is_not_registered():
    result = subprocess.run(
        [sys.executable, "-m", "csegraph._cli", "inspect", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "invalid choice" in result.stderr
