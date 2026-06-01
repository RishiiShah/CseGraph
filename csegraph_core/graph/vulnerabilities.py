"""Vulnerability scanning: detects security anti-patterns using the graph."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from csegraph_core.index.repository import ProjectIndex


SEVERITY_CRITICAL = "critical"
SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"
SEVERITY_INFO = "info"

SEVERITY_ORDER = {
    SEVERITY_CRITICAL: 0,
    SEVERITY_HIGH: 1,
    SEVERITY_MEDIUM: 2,
    SEVERITY_LOW: 3,
    SEVERITY_INFO: 4,
}

_DANGEROUS_SOURCE_PATTERNS: List[Tuple[re.Pattern, str, str, str]] = [
    (re.compile(r"\beval\s*\("), "eval", "injection", "eval() executes arbitrary code"),
    (re.compile(r"\bexec\s*\("), "exec", "injection", "exec() executes arbitrary code"),
    (re.compile(r"\bcompile\s*\("), "compile", "injection", "compile() can execute arbitrary code when combined with eval/exec"),
    (re.compile(r"\bos\.system\s*\("), "os.system", "injection", "os.system() is vulnerable to shell injection"),
    (re.compile(r"\bos\.popen\s*\("), "os.popen", "injection", "os.popen() is vulnerable to shell injection"),
    (re.compile(r"\b__import__\s*\("), "__import__", "injection", "dynamic __import__() can load untrusted modules"),
    (re.compile(r"\bpickle\.loads?\s*\("), "pickle.load", "deserialization", "pickle can execute arbitrary code during deserialization"),
    (re.compile(r"\byaml\.load\s*\("), "yaml.load", "deserialization", "yaml.load can execute arbitrary code; use yaml.safe_load"),
    (re.compile(r"\bmarshal\.loads?\s*\("), "marshal.load", "deserialization", "marshal can execute arbitrary code during deserialization"),
    (re.compile(r"\binnerHTML\s*="), "innerHTML", "xss", "direct HTML injection enables cross-site scripting"),
    (re.compile(r"\bdangerouslySetInnerHTML"), "dangerouslySetInnerHTML", "xss", "direct HTML injection enables cross-site scripting"),
    (re.compile(r"\bdocument\.write\s*\("), "document.write", "xss", "document.write() enables cross-site scripting"),
    (re.compile(r"\bmark_safe\s*\("), "mark_safe", "xss", "mark_safe() bypasses Django auto-escaping"),
    (re.compile(r"\b(?:hashlib\.)?md5\s*\(", re.IGNORECASE), "md5", "weak_crypto", "MD5 is cryptographically broken; use SHA-256+"),
    (re.compile(r"\b(?:hashlib\.)?sha1\s*\(", re.IGNORECASE), "sha1", "weak_crypto", "SHA1 is cryptographically broken; use SHA-256+"),
]

_SECRET_NAME_RE = re.compile(
    r"(api[_-]?key|secret[_-]?key|password|passwd|token|auth[_-]?token|"
    r"private[_-]?key|access[_-]?key|client[_-]?secret|db[_-]?password|"
    r"encryption[_-]?key|jwt[_-]?secret|aws[_-]?secret)",
    re.IGNORECASE,
)

_HARDCODED_SECRET_RE = re.compile(
    r"""(?:=\s*['"])[A-Za-z0-9+/=_\-]{16,}(?:['"])""",
)

_SHELL_TRUE_RE = re.compile(r"shell\s*=\s*True", re.IGNORECASE)

_SECURITY_SENSITIVE_KEYWORDS = {
    "auth", "login", "password", "credential", "token", "session",
    "permission", "encrypt", "decrypt", "hash", "sign", "verify",
    "sanitize", "validate", "escape", "certificate", "ssl", "tls",
    "cookie", "csrf", "cors", "oauth", "jwt", "api_key", "secret",
}


@dataclass
class Vulnerability:
    id: str
    symbol_name: str
    symbol_kind: str
    path: str
    line_range: Optional[List[int]]
    severity: str
    category: str
    description: str
    caller_count: int
    has_test_coverage: bool
    community_id: Optional[int]
    evidence: List[str] = field(default_factory=list)


@dataclass
class VulnerabilityResult:
    command: str
    db_path: str
    repo_root: str
    total_vulnerabilities: int
    critical: List[Vulnerability]
    high: List[Vulnerability]
    medium: List[Vulnerability]
    low: List[Vulnerability]
    info: List[Vulnerability]
    summary: str
    scan_categories: List[str]
    warnings: List[str] = field(default_factory=list)


def _line_range(row) -> Optional[List[int]]:
    s, e = row["start_line"], row["end_line"]
    return [s, e] if s is not None and e is not None else None


def _is_test_path(path: str) -> bool:
    p = path.lower()
    return ("test_" in p or "_test." in p or "/tests/" in p
            or "\\tests\\" in p or p.startswith("test_") or "/test/" in p
            or "\\test\\" in p)


class VulnerabilityService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def scan(self, limit: int = 50) -> VulnerabilityResult:
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            metadata = index.metadata()
            repo_root = metadata["root_dir"]
            return self._scan(index, repo_root, limit)
        finally:
            index.close()

    def _scan(
        self,
        index: ProjectIndex,
        repo_root: str,
        limit: int,
    ) -> VulnerabilityResult:
        warnings: List[str] = []
        all_vulns: List[Vulnerability] = []

        all_vulns.extend(self._check_dangerous_calls(index, warnings))
        all_vulns.extend(self._check_shell_true(index, warnings))
        all_vulns.extend(self._check_hardcoded_secrets(index, warnings))
        all_vulns.extend(self._check_untested_security_code(index, warnings))
        all_vulns.extend(self._check_high_exposure_sensitive(index, warnings))

        seen_keys: Set[str] = set()
        deduped: List[Vulnerability] = []
        for v in all_vulns:
            key = (v.id, v.category, v.severity)
            if key not in seen_keys:
                seen_keys.add(key)
                deduped.append(v)

        deduped.sort(key=lambda v: (SEVERITY_ORDER.get(v.severity, 99), v.symbol_name))

        critical = [v for v in deduped if v.severity == SEVERITY_CRITICAL][:limit]
        high = [v for v in deduped if v.severity == SEVERITY_HIGH][:limit]
        medium = [v for v in deduped if v.severity == SEVERITY_MEDIUM][:limit]
        low = [v for v in deduped if v.severity == SEVERITY_LOW][:limit]
        info = [v for v in deduped if v.severity == SEVERITY_INFO][:limit]

        total = len(critical) + len(high) + len(medium) + len(low) + len(info)
        parts = [f"{total} vulnerability/ies found"]
        if critical:
            parts.append(f"{len(critical)} critical")
        if high:
            parts.append(f"{len(high)} high")
        if medium:
            parts.append(f"{len(medium)} medium")
        if low:
            parts.append(f"{len(low)} low")
        if info:
            parts.append(f"{len(info)} info")

        categories = sorted({v.category for v in deduped})

        return VulnerabilityResult(
            command="vulnerabilities",
            db_path=self.db_path,
            repo_root=repo_root,
            total_vulnerabilities=total,
            critical=critical,
            high=high,
            medium=medium,
            low=low,
            info=info,
            summary=". ".join(parts) + ".",
            scan_categories=categories,
            warnings=warnings,
        )

    def _check_dangerous_calls(
        self, index: ProjectIndex, warnings: List[str],
    ) -> List[Vulnerability]:
        vulns: List[Vulnerability] = []
        repo_root = index.metadata()["root_dir"]

        rows = index.conn.execute(
            """SELECT id, name, type, path, start_line, end_line, community_id
               FROM nodes
               WHERE type IN ('function','method')
                 AND is_test = 0
                 AND start_line IS NOT NULL AND end_line IS NOT NULL""",
        ).fetchall()

        file_cache: Dict[str, List[str]] = {}

        for row in rows:
            if _is_test_path(row["path"]):
                continue

            snippet = self._read_snippet(repo_root, row["path"],
                                         row["start_line"], row["end_line"],
                                         file_cache)
            if snippet is None:
                continue

            for pattern, call_name, category, desc in _DANGEROUS_SOURCE_PATTERNS:
                if pattern.search(snippet):
                    caller_count = self._caller_count(index, row["id"])
                    has_test = self._has_test_coverage(index, row["id"])

                    if caller_count >= 5 and not has_test:
                        severity = SEVERITY_CRITICAL
                    elif not has_test:
                        severity = SEVERITY_HIGH
                    elif caller_count >= 5:
                        severity = SEVERITY_HIGH
                    else:
                        severity = SEVERITY_MEDIUM

                    evidence = [f"calls {call_name}()"]
                    if not has_test:
                        evidence.append("no test coverage")
                    if caller_count > 0:
                        evidence.append(f"{caller_count} caller(s)")

                    vulns.append(Vulnerability(
                        id=row["id"],
                        symbol_name=row["name"],
                        symbol_kind=row["type"],
                        path=row["path"],
                        line_range=_line_range(row),
                        severity=severity,
                        category=category,
                        description=desc,
                        caller_count=caller_count,
                        has_test_coverage=has_test,
                        community_id=row["community_id"],
                        evidence=evidence,
                    ))
                    break

        return vulns

    def _check_shell_true(
        self, index: ProjectIndex, warnings: List[str],
    ) -> List[Vulnerability]:
        vulns: List[Vulnerability] = []
        repo_root = index.metadata()["root_dir"]

        rows = index.conn.execute(
            """SELECT id, name, type, path, start_line, end_line, community_id
               FROM nodes
               WHERE type IN ('function','method')
                 AND is_test = 0
                 AND start_line IS NOT NULL AND end_line IS NOT NULL""",
        ).fetchall()

        file_cache: Dict[str, List[str]] = {}

        for row in rows:
            if _is_test_path(row["path"]):
                continue
            snippet = self._read_snippet(repo_root, row["path"],
                                         row["start_line"], row["end_line"],
                                         file_cache)
            if snippet is None:
                continue
            if not _SHELL_TRUE_RE.search(snippet):
                continue

            caller_count = self._caller_count(index, row["id"])
            has_test = self._has_test_coverage(index, row["id"])
            severity = SEVERITY_CRITICAL if not has_test else SEVERITY_HIGH

            vulns.append(Vulnerability(
                id=row["id"],
                symbol_name=row["name"],
                symbol_kind=row["type"],
                path=row["path"],
                line_range=_line_range(row),
                severity=severity,
                category="injection",
                description="subprocess call with shell=True is vulnerable to command injection",
                caller_count=caller_count,
                has_test_coverage=has_test,
                community_id=row["community_id"],
                evidence=["shell=True in source", "no test coverage" if not has_test else "has tests"],
            ))

        return vulns

    def _check_hardcoded_secrets(
        self, index: ProjectIndex, warnings: List[str],
    ) -> List[Vulnerability]:
        vulns: List[Vulnerability] = []

        rows = index.conn.execute(
            """SELECT id, name, type, path, start_line, end_line, community_id
               FROM nodes
               WHERE type IN ('function','method','class')
                 AND is_test = 0""",
        ).fetchall()

        for row in rows:
            if _is_test_path(row["path"]):
                continue
            if _SECRET_NAME_RE.search(row["name"]):
                has_test = self._has_test_coverage(index, row["id"])
                vulns.append(Vulnerability(
                    id=row["id"],
                    symbol_name=row["name"],
                    symbol_kind=row["type"],
                    path=row["path"],
                    line_range=_line_range(row),
                    severity=SEVERITY_MEDIUM,
                    category="hardcoded_secret",
                    description=f"Symbol name '{row['name']}' suggests it handles secrets; verify no hardcoded credentials",
                    caller_count=self._caller_count(index, row["id"]),
                    has_test_coverage=has_test,
                    community_id=row["community_id"],
                    evidence=[f"name matches secret pattern: {row['name']}"],
                ))

        return vulns

    def _check_untested_security_code(
        self, index: ProjectIndex, warnings: List[str],
    ) -> List[Vulnerability]:
        vulns: List[Vulnerability] = []

        rows = index.conn.execute(
            """SELECT id, name, type, path, start_line, end_line, community_id
               FROM nodes
               WHERE type IN ('function','method')
                 AND is_test = 0""",
        ).fetchall()

        for row in rows:
            if _is_test_path(row["path"]):
                continue
            name_lower = row["name"].lower()
            matched_keywords = [kw for kw in _SECURITY_SENSITIVE_KEYWORDS if kw in name_lower]
            if not matched_keywords:
                continue

            has_test = self._has_test_coverage(index, row["id"])
            if has_test:
                continue

            caller_count = self._caller_count(index, row["id"])
            severity = SEVERITY_HIGH if caller_count >= 3 else SEVERITY_MEDIUM

            vulns.append(Vulnerability(
                id=row["id"],
                symbol_name=row["name"],
                symbol_kind=row["type"],
                path=row["path"],
                line_range=_line_range(row),
                severity=severity,
                category="untested_security",
                description=f"Security-sensitive symbol '{row['name']}' has no test coverage",
                caller_count=caller_count,
                has_test_coverage=False,
                community_id=row["community_id"],
                evidence=[
                    f"matches security keywords: {', '.join(matched_keywords)}",
                    "no test coverage",
                    f"{caller_count} caller(s)" if caller_count > 0 else "no callers",
                ],
            ))

        return vulns

    def _check_high_exposure_sensitive(
        self, index: ProjectIndex, warnings: List[str],
    ) -> List[Vulnerability]:
        vulns: List[Vulnerability] = []
        repo_root = index.metadata()["root_dir"]

        rows = index.conn.execute(
            """SELECT n.id, n.name, n.type, n.path, n.start_line, n.end_line,
                      n.community_id, COUNT(e.id) AS caller_count
               FROM nodes n
               JOIN edges e ON e.target = n.id AND e.relation IN ('calls','inherits')
               WHERE n.type IN ('function','method','class')
                 AND n.is_test = 0
                 AND n.start_line IS NOT NULL AND n.end_line IS NOT NULL
               GROUP BY n.id
               HAVING caller_count >= 10""",
        ).fetchall()

        file_cache: Dict[str, List[str]] = {}

        for row in rows:
            if _is_test_path(row["path"]):
                continue

            snippet = self._read_snippet(repo_root, row["path"],
                                         row["start_line"], row["end_line"],
                                         file_cache)
            if snippet is None:
                continue

            found_dangerous: List[str] = []
            for pattern, call_name, _cat, _desc in _DANGEROUS_SOURCE_PATTERNS:
                if pattern.search(snippet):
                    found_dangerous.append(call_name)

            if not found_dangerous:
                continue

            has_test = self._has_test_coverage(index, row["id"])
            severity = SEVERITY_CRITICAL if not has_test else SEVERITY_HIGH

            cross_comm = self._cross_community_count(index, row["id"], row["community_id"])

            evidence = [
                f"{row['caller_count']} callers (high exposure)",
                f"calls dangerous: {', '.join(sorted(found_dangerous))}",
            ]
            if cross_comm > 0:
                evidence.append(f"{cross_comm} cross-community edge(s)")
            if not has_test:
                evidence.append("no test coverage")

            vulns.append(Vulnerability(
                id=row["id"],
                symbol_name=row["name"],
                symbol_kind=row["type"],
                path=row["path"],
                line_range=_line_range(row),
                severity=severity,
                category="high_exposure",
                description=f"High-exposure symbol ({row['caller_count']} callers) calls dangerous API(s): {', '.join(sorted(found_dangerous))}",
                caller_count=row["caller_count"],
                has_test_coverage=has_test,
                community_id=row["community_id"],
                evidence=evidence,
            ))

        return vulns

    def _read_snippet(
        self,
        repo_root: str,
        rel_path: str,
        start_line: int,
        end_line: int,
        cache: Dict[str, List[str]],
    ) -> Optional[str]:
        if rel_path not in cache:
            src_path = Path(repo_root) / rel_path
            if not src_path.exists():
                cache[rel_path] = []
            else:
                try:
                    cache[rel_path] = src_path.read_text(
                        encoding="utf-8", errors="replace"
                    ).splitlines()
                except OSError:
                    cache[rel_path] = []
        lines = cache[rel_path]
        if not lines:
            return None
        return "\n".join(lines[max(0, start_line - 1):end_line])

    def _caller_count(self, index: ProjectIndex, sym_id: str) -> int:
        return index.conn.execute(
            "SELECT COUNT(*) AS cnt FROM edges WHERE target = ? AND relation IN ('calls','inherits')",
            (sym_id,),
        ).fetchone()["cnt"]

    def _has_test_coverage(self, index: ProjectIndex, sym_id: str) -> bool:
        return index.conn.execute(
            "SELECT COUNT(*) AS cnt FROM edges WHERE (source = ? OR target = ?) AND relation = 'tested_by'",
            (sym_id, sym_id),
        ).fetchone()["cnt"] > 0

    def _cross_community_count(self, index: ProjectIndex, sym_id: str, community_id: Optional[int]) -> int:
        if community_id is None:
            return 0
        return index.conn.execute(
            """SELECT COUNT(*) AS cnt FROM (
                SELECT e.id FROM edges e
                JOIN nodes n ON n.id = e.target
                WHERE e.source = ? AND n.community_id IS NOT NULL AND n.community_id != ?
                UNION ALL
                SELECT e.id FROM edges e
                JOIN nodes n ON n.id = e.source
                WHERE e.target = ? AND n.community_id IS NOT NULL AND n.community_id != ?
            )""",
            (sym_id, community_id, sym_id, community_id),
        ).fetchone()["cnt"]
