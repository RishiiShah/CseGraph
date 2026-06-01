"""Tests for vulnerability scanning service."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from csegraph_core.core.models import to_dict
from csegraph_core.graph.vulnerabilities import VulnerabilityService
from csegraph_core.index.services import IndexService
from csegraph_core.postprocess import PostprocessService


def _index_repo(tmp_path: Path, repo: Path) -> str:
    db = str(tmp_path / "index.db")
    IndexService(db).index(str(repo), profile="small")
    return db


class TestVulnerabilities:
    def test_no_vulnerabilities_clean_code(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "clean.py").write_text(
            "def add(a, b):\n    return a + b\n\ndef multiply(a, b):\n    return a * b\n",
            encoding="utf-8",
        )
        db = _index_repo(tmp_path, repo)

        result = VulnerabilityService(db).scan()

        assert result.command == "vulnerabilities"
        assert result.total_vulnerabilities == 0
        assert result.critical == []
        assert result.high == []

    def test_detects_eval_call(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "danger.py").write_text(
            "def run_user_code(code):\n    return eval(code)\n",
            encoding="utf-8",
        )
        db = _index_repo(tmp_path, repo)

        result = VulnerabilityService(db).scan()

        all_vulns = result.critical + result.high + result.medium + result.low
        vuln_names = {v.symbol_name for v in all_vulns}
        assert "run_user_code" in vuln_names
        matching = [v for v in all_vulns if v.symbol_name == "run_user_code"]
        assert any(v.category == "injection" for v in matching)
        assert any("eval" in " ".join(v.evidence) for v in matching)

    def test_detects_exec_call(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "danger.py").write_text(
            "def execute_dynamic(code):\n    exec(code)\n",
            encoding="utf-8",
        )
        db = _index_repo(tmp_path, repo)

        result = VulnerabilityService(db).scan()

        all_vulns = result.critical + result.high + result.medium + result.low
        assert any(v.symbol_name == "execute_dynamic" and v.category == "injection" for v in all_vulns)

    def test_detects_system_call(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "cmd.py").write_text(
            "import os\ndef run_cmd(cmd):\n    os.system(cmd)\n",
            encoding="utf-8",
        )
        db = _index_repo(tmp_path, repo)

        result = VulnerabilityService(db).scan()

        all_vulns = result.critical + result.high + result.medium + result.low
        vuln_names = {v.symbol_name for v in all_vulns}
        assert "run_cmd" in vuln_names

    def test_detects_secret_pattern_in_name(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "config.py").write_text(
            "def get_api_key():\n    return 'abc123'\n\ndef get_db_password():\n    return 'pass'\n",
            encoding="utf-8",
        )
        db = _index_repo(tmp_path, repo)

        result = VulnerabilityService(db).scan()

        all_vulns = result.critical + result.high + result.medium + result.low + result.info
        vuln_names = {v.symbol_name for v in all_vulns}
        assert "get_api_key" in vuln_names or "get_db_password" in vuln_names
        matching = [v for v in all_vulns if v.category == "hardcoded_secret"]
        assert len(matching) >= 1

    def test_untested_security_code(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "auth.py").write_text(
            "def validate_token(token):\n    return True\n\ndef check_permission(user):\n    return True\n",
            encoding="utf-8",
        )
        db = _index_repo(tmp_path, repo)

        result = VulnerabilityService(db).scan()

        all_vulns = result.critical + result.high + result.medium + result.low
        matching = [v for v in all_vulns if v.category == "untested_security"]
        names = {v.symbol_name for v in matching}
        assert "validate_token" in names or "check_permission" in names

    def test_tested_security_code_not_flagged(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "auth.py").write_text(
            "def validate_token(token):\n    return True\n",
            encoding="utf-8",
        )
        (repo / "test_auth.py").write_text(
            "from auth import validate_token\ndef test_validate():\n    validate_token('abc')\n",
            encoding="utf-8",
        )
        db = _index_repo(tmp_path, repo)

        result = VulnerabilityService(db).scan()

        untested_vulns = [v for v in result.medium + result.high + result.critical
                         if v.category == "untested_security" and v.symbol_name == "validate_token"]
        assert len(untested_vulns) == 0

    def test_test_files_excluded(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "test_runner.py").write_text(
            "def test_eval_works():\n    eval('1+1')\n",
            encoding="utf-8",
        )
        db = _index_repo(tmp_path, repo)

        result = VulnerabilityService(db).scan()

        assert result.total_vulnerabilities == 0

    def test_severity_escalation_untested_with_callers(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "core.py").write_text(
            "def dangerous():\n    return eval('1')\n",
            encoding="utf-8",
        )
        callers = "\n".join(
            f"from core import dangerous\ndef caller_{i}():\n    dangerous()\n"
            for i in range(6)
        )
        (repo / "users.py").write_text(callers, encoding="utf-8")
        db = _index_repo(tmp_path, repo)

        result = VulnerabilityService(db).scan()

        danger_vulns = [v for v in result.critical + result.high
                        if v.symbol_name == "dangerous"]
        assert len(danger_vulns) >= 1
        assert any(v.severity in ("critical", "high") for v in danger_vulns)

    def test_limit_respected(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        funcs = "\n".join(
            f"def auth_func_{i}():\n    pass\n" for i in range(10)
        )
        (repo / "auth.py").write_text(funcs, encoding="utf-8")
        db = _index_repo(tmp_path, repo)

        result = VulnerabilityService(db).scan(limit=3)
        for level in [result.critical, result.high, result.medium, result.low, result.info]:
            assert len(level) <= 3

    def test_serializable(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "danger.py").write_text(
            "def run_it():\n    eval('1')\n",
            encoding="utf-8",
        )
        db = _index_repo(tmp_path, repo)

        result = VulnerabilityService(db).scan()
        payload = to_dict(result)
        serialized = json.dumps(payload)
        assert isinstance(serialized, str)
        parsed = json.loads(serialized)
        assert "total_vulnerabilities" in parsed
        assert "critical" in parsed

    def test_empty_index(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "readme.txt").write_text("not code", encoding="utf-8")
        db = _index_repo(tmp_path, repo)

        result = VulnerabilityService(db).scan()

        assert result.total_vulnerabilities == 0
        assert result.scan_categories == []

    def test_scan_categories_populated(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "mixed.py").write_text(
            "def get_secret_key():\n    return 'key'\n\ndef run_eval(x):\n    eval(x)\n",
            encoding="utf-8",
        )
        db = _index_repo(tmp_path, repo)

        result = VulnerabilityService(db).scan()

        assert len(result.scan_categories) >= 1

    def test_multiple_categories(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "app.py").write_text(
            "def get_api_key():\n    return eval('key')\n\n"
            "def validate_session(s):\n    pass\n",
            encoding="utf-8",
        )
        db = _index_repo(tmp_path, repo)

        result = VulnerabilityService(db).scan()

        categories = {v.category for v in
                      result.critical + result.high + result.medium + result.low + result.info}
        assert len(categories) >= 2

    def test_community_context(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "a.py").write_text(
            "def auth_handler():\n    eval('x')\n",
            encoding="utf-8",
        )
        (repo / "b.py").write_text(
            "from a import auth_handler\ndef caller():\n    auth_handler()\n",
            encoding="utf-8",
        )
        db = _index_repo(tmp_path, repo)
        PostprocessService(db).postprocess()

        result = VulnerabilityService(db).scan()

        all_vulns = result.critical + result.high + result.medium + result.low
        assert len(all_vulns) >= 1
