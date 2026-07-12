from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from csegraph._cli.main import _build_parser, _dispatch


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


def test_plain_refresh_uses_candidate_aware_coordinator(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    expected = object()
    calls: list[tuple[str, Path]] = []

    class RecordingCoordinator:
        def __init__(self, db_path: str) -> None:
            calls.append((db_path, Path()))

        def explicit_refresh(self, repo_path: str) -> object:
            calls[-1] = (calls[-1][0], Path(repo_path))
            return expected

    class FailingRefreshService:
        def __init__(self, db_path: str) -> None:
            raise AssertionError("plain refresh should use candidate detection")

    monkeypatch.setattr(
        "csegraph._core.retrieval.freshness.FreshnessCoordinator",
        RecordingCoordinator,
    )
    monkeypatch.setattr(
        "csegraph._core.index.services.RefreshService",
        FailingRefreshService,
    )
    args = _build_parser().parse_args(["refresh", str(repo), "--json"])

    result = _dispatch(args)

    assert result is expected
    assert calls == [(str(repo / ".csegraph" / "index.db"), repo)]


def test_refresh_with_membership_overrides_keeps_full_discovery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    expected = object()
    calls: list[dict[str, object]] = []

    class RecordingRefreshService:
        def __init__(self, db_path: str) -> None:
            assert db_path == str(repo / ".csegraph" / "index.db")

        def refresh(self, **kwargs: object) -> object:
            calls.append(kwargs)
            return expected

    class FailingCoordinator:
        def __init__(self, db_path: str) -> None:
            raise AssertionError("membership overrides require full discovery")

    monkeypatch.setattr(
        "csegraph._core.index.services.RefreshService",
        RecordingRefreshService,
    )
    monkeypatch.setattr(
        "csegraph._core.retrieval.freshness.FreshnessCoordinator",
        FailingCoordinator,
    )
    args = _build_parser().parse_args(
        [
            "refresh",
            str(repo),
            "--exclude",
            "*.generated.py",
            "--include-root",
            "src",
            "--json",
        ]
    )

    result = _dispatch(args)

    assert result is expected
    assert calls == [
        {
            "exclude_patterns": ["*.generated.py"],
            "include_roots": ["src"],
        }
    ]
