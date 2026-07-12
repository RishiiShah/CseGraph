"""Materialize the pinned open-source benchmark sandbox under sandbox/."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.benchmarks.sandbox import SANDBOX_REPOSITORIES, SandboxRepositorySpec


def bootstrap_sandbox(
    destination: Path,
    *,
    specs: Iterable[SandboxRepositorySpec] = SANDBOX_REPOSITORIES,
    dry_run: bool = False,
) -> list[dict[str, object]]:
    destination = destination.resolve()
    report: list[dict[str, object]] = []
    for spec in specs:
        target = destination / spec.path
        if target.is_dir():
            observed = _git_commit(target)
            if observed != spec.commit:
                _run(
                    ["git", "-C", str(target), "fetch", "--depth=1", "origin", spec.commit],
                    dry_run=dry_run,
                )
                _run(
                    ["git", "-C", str(target), "checkout", "--detach", spec.commit],
                    dry_run=dry_run,
                )
                observed = spec.commit if dry_run else _git_commit(target)
            report.append(
                {
                    "path": spec.path,
                    "url": spec.url,
                    "expected_commit": spec.commit,
                    "observed_commit": observed,
                    "status": "present" if observed == spec.commit else "mismatch",
                }
            )
            continue
        if dry_run:
            report.append(
                {
                    "path": spec.path,
                    "url": spec.url,
                    "expected_commit": spec.commit,
                    "observed_commit": None,
                    "status": "would_clone",
                }
            )
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        _run(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                spec.url,
                str(target),
            ],
            dry_run=False,
        )
        _run(["git", "-C", str(target), "checkout", "--detach", spec.commit], dry_run=False)
        observed = _git_commit(target)
        report.append(
            {
                "path": spec.path,
                "url": spec.url,
                "expected_commit": spec.commit,
                "observed_commit": observed,
                "status": "ready" if observed == spec.commit else "mismatch",
            }
        )
    return report


def _run(command: list[str], *, dry_run: bool) -> None:
    if dry_run:
        return
    subprocess.run(command, check=True, timeout=900)


def _git_commit(path: Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            text=True,
            timeout=15,
        ).strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", default=str(REPO_ROOT))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    report = bootstrap_sandbox(Path(args.destination), dry_run=args.dry_run)
    for item in report:
        print(
            f"{item['status']}: {item['path']} {item['observed_commit'] or item['expected_commit']}"
        )
    return 0 if all(item["status"] in {"ready", "present", "would_clone"} for item in report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
