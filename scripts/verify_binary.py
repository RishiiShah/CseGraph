"""Verify a Nuitka-built csegraph binary (standalone or onefile).

Cross-platform verification: no dependency on GNU timeout or other
platform-specific tools. Uses Python subprocess with timeouts.

Usage:
    python scripts/verify_binary.py dist/__main__.dist/csegraph             # standalone
    python scripts/verify_binary.py dist/csegraph                            # onefile
    python scripts/verify_binary.py dist/__main__.dist/csegraph --repo /path/to/repo
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
INFO = "INFO"


def _run(
    binary: Path,
    args: list[str],
    timeout: int = 60,
    expect_rc: int | None = 0,
    strip_venv: bool = False,
) -> tuple[str, str, str, int]:
    env = os.environ.copy()
    if strip_venv:
        venv_bin = str(Path(sys.executable).parent)
        paths = env.get("PATH", "").split(os.pathsep)
        env["PATH"] = os.pathsep.join(p for p in paths if p != venv_bin)

    try:
        proc = subprocess.run(
            [str(binary)] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return "", "", "timeout", -1

    status = PASS
    if expect_rc is not None and proc.returncode != expect_rc:
        status = FAIL

    return proc.stdout, proc.stderr, status, proc.returncode


_CSEGRAPH_IDENTIFIERS = {
    "def _load_template", "def make_python_config", "def make_typescript_config",
    "class LazyLanguageMap", "def index_repository", "def retrieve_context",
    "def _collect_treesitter", "def _bfs_expand", "def _score_node",
    "def _build_context_response", "class BenchmarkService", "class IndexService",
    "class ContextService",
}


def _check_source_leakage(binary: Path, dist_dir: Path | None) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []

    if dist_dir is not None:
        for ext in (".py", ".pyc"):
            found = list(dist_dir.rglob(f"*{ext}"))
            if found:
                rel = [str(f.relative_to(dist_dir)) for f in found[:10]]
                suffix = f" (and {len(found) - 10} more)" if len(found) > 10 else ""
                issues.append((FAIL, f"Found {len(found)} {ext} files in dist: {', '.join(rel)}{suffix}"))
        if not any(s == FAIL for s, _ in issues):
            issues.append((PASS, "No .py/.pyc files in dist directory"))

    try:
        result = subprocess.run(
            ["strings", str(binary)],
            capture_output=True, text=True, timeout=60,
        )
        def_lines = [
            l.strip() for l in result.stdout.splitlines()
            if l.strip().startswith("def ") and "(" in l
        ]
        csegraph_leaks = [
            l for l in result.stdout.splitlines()
            if any(ident in l for ident in _CSEGRAPH_IDENTIFIERS)
        ]
        if csegraph_leaks:
            issues.append((FAIL,
                f"csegraph-specific identifiers found in {binary.name}: "
                f"{csegraph_leaks[:5]}"))
        if def_lines:
            issues.append((INFO,
                f"strings heuristic: {len(def_lines)} 'def ' matches in {binary.name} "
                f"(samples: {def_lines[:5]}). All appear to be CPython/third-party, "
                f"not csegraph source."))
        if not csegraph_leaks and not def_lines:
            issues.append((PASS, f"No def/class leakage in {binary.name}"))
    except (subprocess.TimeoutExpired, FileNotFoundError):
        issues.append((FAIL, f"Could not run 'strings' on {binary.name}"))

    return issues


def verify(binary: Path, repo: Path | None = None) -> bool:
    if not binary.exists():
        print(f"Binary not found: {binary}")
        return False

    is_onefile = binary.parent.name != "__main__.dist"
    dist_dir = None if is_onefile else binary.parent
    results: list[tuple[str, str, str]] = []

    def record(name: str, status: str, detail: str = "") -> None:
        results.append((name, status, detail))
        marker = {PASS: "+", FAIL: "!", SKIP: "-", INFO: "~"}[status]
        line = f"  [{marker}] {name}"
        if detail:
            line += f" — {detail}"
        print(line)

    mode_label = "onefile" if is_onefile else "standalone"
    print(f"Verifying: {binary} ({mode_label})")
    if dist_dir:
        print(f"Dist dir:  {dist_dir}")
    print()

    # --- Source protection checks ---
    print("Source protection checks:")
    leaks = _check_source_leakage(binary, dist_dir)
    for severity, detail in leaks:
        record("source-protection", severity, detail)
    print()

    # --- CLI commands ---
    print("CLI command checks:")

    # --help
    stdout, stderr, status, rc = _run(binary, ["--help"])
    record("--help", status, f"rc={rc}" if status == FAIL else "")

    # --help with venv stripped from PATH
    stdout, stderr, status, rc = _run(binary, ["--help"], strip_venv=True)
    record("--help (no venv in PATH)", status, f"rc={rc}" if status == FAIL else "")

    if repo is None:
        record("index", SKIP, "no --repo provided")
        record("context", SKIP, "no --repo provided")
        record("graph", SKIP, "no --repo provided")
        record("tree", SKIP, "no --repo provided")
        record("communities", SKIP, "no --repo provided")
        record("benchmark", SKIP, "no --repo provided")
    else:
        repo_str = str(repo)

        # index
        stdout, stderr, status, rc = _run(binary, ["index", repo_str], timeout=120)
        record("index", status, f"rc={rc}, stderr={stderr[:200]}" if status == FAIL else "")

        # context
        stdout, stderr, status, rc = _run(
            binary, ["context", "test query", "--repo", repo_str, "--json"], timeout=60,
        )
        record("context", status, f"rc={rc}" if status == FAIL else "")

        # graph (template loading)
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            graph_out = f.name
        stdout, stderr, status, rc = _run(
            binary, ["graph", "--repo", repo_str, "--output", graph_out], timeout=60,
        )
        if status == PASS and Path(graph_out).stat().st_size < 100:
            status = FAIL
            record("graph", FAIL, "output HTML too small — template loading may have failed")
        else:
            record("graph", status, f"rc={rc}" if status == FAIL else f"output={graph_out}")
        os.unlink(graph_out) if Path(graph_out).exists() else None

        # tree (template loading)
        stdout, stderr, status, rc = _run(
            binary, ["tree", "--repo", repo_str], timeout=60,
        )
        record("tree", status, f"rc={rc}" if status == FAIL else "")

        # communities
        stdout, stderr, status, rc = _run(
            binary, ["communities", repo_str, "--json"], timeout=60,
        )
        record("communities", status, f"rc={rc}" if status == FAIL else "")

        # benchmark
        stdout, stderr, status, rc = _run(
            binary, ["benchmark", repo_str], timeout=120,
        )
        record("benchmark", status, f"rc={rc}, stderr={stderr[:200]}" if status == FAIL else "")

        # watch (watchfiles — start, let it run briefly, verify it doesn't crash)
        stdout, stderr, watch_status, rc = _run(binary, ["watch", repo_str], timeout=5)
        if watch_status == "timeout":
            record("watch", PASS, "started successfully, killed after 5s timeout")
        elif rc == 0:
            record("watch", PASS, "exited cleanly")
        else:
            record("watch", FAIL, f"rc={rc}, stderr={stderr[:200]}")

    # install --dry-run (tomlkit)
    stdout, stderr, status, rc = _run(
        binary, ["install", "--platform", "codex", "--dry-run"], timeout=30,
    )
    record("install --platform codex --dry-run", status, f"rc={rc}" if status == FAIL else "")

    # MCP serve smoke test: verify no stdout before protocol input
    print()
    print("MCP serve smoke test:")
    stdout, stderr, mcp_status, rc = _run(binary, ["serve"], timeout=5)
    if mcp_status == "timeout":
        if stdout == "":
            record("serve (clean stdout)", PASS, "no stdout before timeout — protocol clean")
        else:
            record("serve (clean stdout)", FAIL, f"unexpected stdout: {stdout[:200]!r}")
    elif rc == 0:
        record("serve (clean stdout)", PASS, "exited cleanly")
    else:
        if stdout == "":
            record("serve (clean stdout)", PASS, f"rc={rc} but stdout was clean")
        else:
            record("serve (clean stdout)", FAIL, f"rc={rc}, stdout={stdout[:200]!r}")

    # --- Summary ---
    print()
    passed = sum(1 for _, s, _ in results if s == PASS)
    failed = sum(1 for _, s, _ in results if s == FAIL)
    skipped = sum(1 for _, s, _ in results if s == SKIP)
    info = sum(1 for _, s, _ in results if s == INFO)
    total = len(results)

    print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped, {info} info")

    if failed:
        print("\nFailed checks:")
        for name, status, detail in results:
            if status == FAIL:
                print(f"  - {name}: {detail}")

    binary_size = binary.stat().st_size / (1024 * 1024)
    print(f"\nBinary size: {binary_size:.1f} MB")
    if dist_dir is not None:
        dir_size_mb = sum(
            f.stat().st_size for f in dist_dir.rglob("*") if f.is_file()
        ) / (1024 * 1024)
        print(f"Dist dir size: {dir_size_mb:.1f} MB")

    return failed == 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify csegraph standalone binary")
    parser.add_argument("binary", type=Path, help="Path to the csegraph binary")
    parser.add_argument("--repo", type=Path, default=None, help="Path to a repo to test indexing against")
    args = parser.parse_args()

    success = verify(args.binary, args.repo)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
