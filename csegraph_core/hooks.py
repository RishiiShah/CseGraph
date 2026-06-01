"""Git hook helpers for csegraph auto-refresh."""
from __future__ import annotations

import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List


HOOK_MARKER = "# csegraph-auto-refresh"

HOOK_SCRIPT = f"""\
#!/bin/sh
{HOOK_MARKER}
# Auto-refresh csegraph index after git operations.
# Installed by: csegraph install --hooks
if command -v csegraph >/dev/null 2>&1; then
    csegraph refresh . --profile small 2>/dev/null &
fi
"""

HOOK_NAMES = ["post-commit", "post-merge", "post-checkout"]


@dataclass
class HooksResult:
    command: str
    repo_root: str
    hooks_dir: str
    installed: List[str]
    skipped: List[str]


def find_git_dir(repo: str | Path) -> Path:
    p = Path(repo).resolve()
    while True:
        git = p / ".git"
        if git.is_dir():
            return git
        if git.is_file():
            text = git.read_text(encoding="utf-8").strip()
            if text.startswith("gitdir:"):
                rel_path = Path(text.split(":", 1)[1].strip())
                return (git.parent / rel_path).resolve()
        parent = p.parent
        if parent == p:
            raise FileNotFoundError(f"No .git directory found from {repo}")
        p = parent


def install_hooks(repo: str | Path) -> HooksResult:
    repo_path = Path(repo).resolve()
    git_dir = find_git_dir(repo_path)
    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    installed: List[str] = []
    skipped: List[str] = []

    for name in HOOK_NAMES:
        hook_path = hooks_dir / name
        if hook_path.exists():
            content = hook_path.read_text(encoding="utf-8")
            if HOOK_MARKER in content:
                skipped.append(name)
                continue
            content = content.rstrip() + "\n\n" + HOOK_SCRIPT
            hook_path.write_text(content, encoding="utf-8")
        else:
            hook_path.write_text(HOOK_SCRIPT, encoding="utf-8")

        if os.name != "nt":
            hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC)

        installed.append(name)

    return HooksResult(
        command="hooks install",
        repo_root=str(repo_path),
        hooks_dir=str(hooks_dir),
        installed=installed,
        skipped=skipped,
    )


def uninstall_hooks(repo: str | Path) -> HooksResult:
    repo_path = Path(repo).resolve()
    git_dir = find_git_dir(repo_path)
    hooks_dir = git_dir / "hooks"

    removed: List[str] = []
    skipped: List[str] = []

    block_to_remove = f"""\
{HOOK_MARKER}
# Auto-refresh csegraph index after git operations.
# Installed by: csegraph install --hooks
if command -v csegraph >/dev/null 2>&1; then
    csegraph refresh . --profile small 2>/dev/null &
fi"""

    for name in HOOK_NAMES:
        hook_path = hooks_dir / name
        if not hook_path.exists():
            skipped.append(name)
            continue
        content = hook_path.read_text(encoding="utf-8")
        if HOOK_MARKER not in content:
            skipped.append(name)
            continue

        new_content = content
        for prefix in ("\n\n", "\n", ""):
            target = prefix + block_to_remove
            if target in new_content:
                new_content = new_content.replace(target, "")
                break
        else:
            new_content = new_content.replace(block_to_remove, "")

        remaining = new_content.strip()
        if remaining and remaining != "#!/bin/sh":
            hook_path.write_text(remaining + "\n", encoding="utf-8")
        else:
            hook_path.unlink()
        removed.append(name)

    return HooksResult(
        command="hooks uninstall",
        repo_root=str(repo_path),
        hooks_dir=str(hooks_dir),
        installed=removed,
        skipped=skipped,
    )
