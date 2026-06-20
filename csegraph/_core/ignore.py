"""Git-aware ignore handling for CseGraph discovery.

Discovery uses ``git ls-files`` when a git repo is present, else ``svn list -R``
for SVN working copies, else a bounded directory walk. ``.csegraphignore``
excludes paths from the VCS candidate set. ``.gitignore`` still applies on
directory walks and for ignore-rule unit tests. Entrypoint: ``load_ignore_filter(root)``.
"""

from __future__ import annotations

import fnmatch
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import List, Optional, Sequence, Set

IGNORE_FILENAME = ".csegraphignore"
GITIGNORE_FILENAME = ".gitignore"
_GIT_TIMEOUT = 10
_ENV_RECURSE_SUBMODULES = "CSEGRAPH_RECURSE_SUBMODULES"


def recurse_submodules_enabled(explicit: Optional[bool] = None) -> bool:
    """Whether ``git ls-files`` should pass ``--recurse-submodules``.

    Defaults to enabled so pulled submodule code is indexed. Set
    ``CSEGRAPH_RECURSE_SUBMODULES=0`` to disable (large vendor submodules).
    """
    if explicit is not None:
        return explicit
    raw = os.environ.get(_ENV_RECURSE_SUBMODULES, "1").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    return True


@dataclass(frozen=True)
class IgnoreRule:
    source: str
    anchor: Path
    pattern: str
    dir_only: bool
    anchored: bool
    negated: bool


@dataclass(frozen=True)
class IgnoreDecision:
    matched: bool
    ignored: bool
    source: Optional[str] = None
    negated: bool = False


def load_ignore_filter(
    root: Path,
    *,
    recurse_submodules: Optional[bool] = None,
    exclude_patterns: Optional[Sequence[str]] = None,
) -> "IgnoreFilter":
    from csegraph._core.vcs import find_svn_root, svn_versioned_paths

    root = root.resolve()
    git_root = _git_root(root)
    vcs: Optional[str] = None
    vcs_root: Optional[Path] = None
    tracked_paths: Set[str] = set()
    tracked_dirs: Set[str] = set()

    if git_root is not None and root.is_relative_to(git_root):
        vcs = "git"
        vcs_root = git_root
        tracked_paths = _git_tracked_paths(
            git_root,
            root,
            recurse_submodules=recurse_submodules_enabled(recurse_submodules),
        )
    else:
        svn_root = find_svn_root(root)
        if svn_root is not None and root.is_relative_to(svn_root):
            tracked_paths = svn_versioned_paths(svn_root, root)
            if tracked_paths:
                vcs = "svn"
                vcs_root = svn_root

    if vcs_root is not None:
        tracked_dirs = _tracked_parent_dirs(tracked_paths)
        search_dirs = _ancestor_dirs(vcs_root, root)
    else:
        search_dirs = [root]

    rules: List[IgnoreRule] = []
    for directory in search_dirs:
        rules.extend(_rules_from_file(directory / GITIGNORE_FILENAME, "gitignore"))
        rules.extend(_rules_from_file(directory / IGNORE_FILENAME, "csegraphignore"))
    for pattern in exclude_patterns or ():
        parsed = _parse_line(pattern.strip(), source="runtime", anchor=root)
        if parsed is not None:
            rules.append(parsed)

    return IgnoreFilter(
        rules,
        root=root,
        vcs=vcs,
        tracked_paths=tracked_paths,
        tracked_dirs=tracked_dirs,
    )


def _parse_line(
    line: str, *, source: str = "csegraphignore", anchor: Optional[Path] = None
) -> Optional[IgnoreRule]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None

    negated = False
    if stripped.startswith("!"):
        negated = True
        stripped = stripped[1:]
        if not stripped:
            return None

    dir_only = stripped.endswith("/")
    if dir_only:
        stripped = stripped.rstrip("/")

    anchored = stripped.startswith("/")
    if anchored:
        stripped = stripped.lstrip("/")
    elif "/" in stripped:
        anchored = True

    if not stripped:
        return None

    return IgnoreRule(
        source=source,
        anchor=anchor or Path("/"),
        pattern=stripped,
        dir_only=dir_only,
        anchored=anchored,
        negated=negated,
    )


class IgnoreFilter:
    __slots__ = ("_rules", "_root", "_vcs", "_tracked_paths", "_tracked_dirs", "_has_negation")

    def __init__(
        self,
        rules: List[IgnoreRule],
        *,
        root: Path = Path("/"),
        vcs: Optional[str] = None,
        git_repo: bool = False,
        tracked_paths: Optional[Set[str]] = None,
        tracked_dirs: Optional[Set[str]] = None,
    ) -> None:
        self._rules = rules
        self._root = root
        self._vcs = vcs or ("git" if git_repo else None)
        self._tracked_paths = tracked_paths or set()
        self._tracked_dirs = tracked_dirs or set()
        self._has_negation = any(rule.negated for rule in rules)

    @classmethod
    def from_lines(cls, lines: List[str]) -> "IgnoreFilter":
        rules: List[IgnoreRule] = []
        for line in lines:
            parsed = _parse_line(line)
            if parsed is not None:
                rules.append(parsed)
        return cls(rules)

    @classmethod
    def from_file(cls, path: Path) -> "IgnoreFilter":
        if not path.is_file():
            return cls([])
        try:
            text = path.read_text(encoding="utf-8")
            rules = [
                parsed
                for line in text.splitlines()
                if (parsed := _parse_line(line, anchor=path.parent.resolve())) is not None
            ]
            return cls(rules, root=path.parent.resolve())
        except Exception:
            return cls([])

    def is_ignored(self, rel_path: str, *, is_dir: bool = False) -> bool:
        rel_path = _normalize_rel(rel_path)
        if not rel_path or not self._rules:
            return False
        rules = self._effective_rules(rel_path, is_dir=is_dir)
        return self._is_ignored_by_rules(rel_path, is_dir=is_dir, rules=rules).ignored

    @property
    def vcs(self) -> Optional[str]:
        """``git``, ``svn``, or ``None`` when discovery falls back to a directory walk."""
        return self._vcs

    @property
    def git_repo(self) -> bool:
        return self._vcs == "git"

    @property
    def svn_repo(self) -> bool:
        return self._vcs == "svn"

    @property
    def index_paths(self) -> Set[str]:
        """Repo-relative paths from the active VCS listing under the scan root."""
        return self._tracked_paths

    def should_descend(self, rel_dir: str) -> bool:
        rel_dir = _normalize_rel(rel_dir)
        if not rel_dir:
            return True
        if self._has_negation:
            return True
        csegraph_rules = [rule for rule in self._rules if rule.source == "csegraphignore"]
        if (
            csegraph_rules
            and self._is_ignored_by_rules(rel_dir, is_dir=True, rules=csegraph_rules).ignored
        ):
            return False
        if rel_dir in self._tracked_dirs:
            return True
        return not self.is_ignored(rel_dir, is_dir=True)

    def _effective_rules(self, rel_path: str, *, is_dir: bool) -> Sequence[IgnoreRule]:
        if self._vcs and not is_dir and rel_path in self._tracked_paths:
            return [rule for rule in self._rules if rule.source in ("csegraphignore", "runtime")]
        return self._rules

    def _is_ignored_by_rules(
        self,
        rel_path: str,
        *,
        is_dir: bool,
        rules: Sequence[IgnoreRule],
    ) -> IgnoreDecision:
        for parent in _parent_dirs(rel_path):
            parent_decision = self._evaluate_rules(parent, is_dir=True, rules=rules)
            if parent_decision.ignored:
                return parent_decision
        return self._evaluate_rules(rel_path, is_dir=is_dir, rules=rules)

    def _evaluate_rules(
        self,
        rel_path: str,
        *,
        is_dir: bool,
        rules: Sequence[IgnoreRule],
    ) -> IgnoreDecision:
        matched = False
        ignored = False
        last_source: Optional[str] = None
        last_negated = False
        for rule in rules:
            if rule.dir_only and not is_dir:
                continue
            if self._matches_rule(rel_path, rule):
                matched = True
                ignored = not rule.negated
                last_source = rule.source
                last_negated = rule.negated
        return IgnoreDecision(
            matched=matched, ignored=ignored, source=last_source, negated=last_negated
        )

    def _matches_rule(self, rel_path: str, rule: IgnoreRule) -> bool:
        target_abs = self._root.joinpath(*PurePosixPath(rel_path).parts)
        if rule.anchored:
            try:
                anchor_rel = target_abs.relative_to(rule.anchor).as_posix()
            except ValueError:
                return False
            return fnmatch.fnmatch(anchor_rel, rule.pattern)
        basename = PurePosixPath(rel_path).name
        return fnmatch.fnmatch(basename, rule.pattern)


def _rules_from_file(path: Path, source: str) -> List[IgnoreRule]:
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    rules: List[IgnoreRule] = []
    for line in lines:
        parsed = _parse_line(line, source=source, anchor=path.parent.resolve())
        if parsed is not None:
            rules.append(parsed)
    return rules


def _git_root(root: Path) -> Optional[Path]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return Path(value).resolve() if value else None


def _git_tracked_paths(
    git_root: Path,
    scan_root: Path,
    *,
    recurse_submodules: bool = True,
) -> Set[str]:
    cmd = ["git", "ls-files", "-z"]
    if recurse_submodules:
        cmd.append("--recurse-submodules")
    try:
        result = subprocess.run(
            cmd,
            cwd=git_root,
            check=False,
            capture_output=True,
            timeout=_GIT_TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return set()
    if result.returncode != 0:
        return set()
    try:
        scan_prefix = scan_root.relative_to(git_root).as_posix()
    except ValueError:
        return set()
    tracked: Set[str] = set()
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        git_rel = raw.decode("utf-8", errors="replace")
        scan_rel = _to_scan_relative(git_rel, scan_prefix)
        if scan_rel:
            tracked.add(scan_rel)
    return tracked


def _to_scan_relative(git_rel: str, scan_prefix: str) -> Optional[str]:
    if not scan_prefix or scan_prefix == ".":
        return git_rel
    if git_rel == scan_prefix:
        return ""
    prefix = f"{scan_prefix}/"
    if git_rel.startswith(prefix):
        return git_rel[len(prefix) :]
    return None


def _tracked_parent_dirs(tracked_paths: Set[str]) -> Set[str]:
    dirs: Set[str] = set()
    for rel_path in tracked_paths:
        parts = PurePosixPath(rel_path).parts[:-1]
        for index in range(1, len(parts) + 1):
            dirs.add("/".join(parts[:index]))
    return dirs


def _ancestor_dirs(ceiling: Path, root: Path) -> List[Path]:
    if not root.is_relative_to(ceiling):
        return [root]
    dirs: List[Path] = []
    current = root
    while True:
        dirs.append(current)
        if current == ceiling:
            break
        current = current.parent
    dirs.reverse()
    return dirs

def _normalize_rel(path: str) -> str:
    normalized = str(PurePosixPath(path.replace("\\", "/"))).strip("/")
    if normalized == ".":
        return ""
    return normalized


def _parent_dirs(rel_path: str) -> List[str]:
    parts = PurePosixPath(rel_path).parts
    parents: List[str] = []
    for index in range(1, len(parts)):
        parents.append("/".join(parts[:index]))
    return parents
