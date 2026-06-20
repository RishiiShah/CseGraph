"""Integration tests for P3 — token reduction benchmark."""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

import pytest

from csegraph._core.benchmark import (
    BenchmarkService,
    _count_diff_tokens,
    _count_raw_tokens,
    _run_corpus_task,
)
from csegraph._core.core.models import (
    BenchmarkCorpusTask,
    ContextNode,
    ContextRelationship,
    ContextResult,
    ImportPrelude,
    SufficiencyResult,
    to_dict,
)
from csegraph._core.cse.metrics import SufficiencyMetrics


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        'from helpers import fmt\n\ndef greet(name: str) -> str:\n    """Say hello."""\n    return fmt(name)\n',
        encoding="utf-8",
    )
    (repo / "helpers.py").write_text(
        'def fmt(name: str) -> str:\n    return f"Hello, {name}"\n',
        encoding="utf-8",
    )
    return repo


def _scratch_path(repo: Path, name: str) -> Path:
    return repo / ".scratch" / "csegraph" / name


def _make_corpus_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "corpus_repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "from helpers import format_name\n"
        "from storage import save_user\n\n"
        "def create_user(name: str) -> dict:\n"
        "    user = {'name': format_name(name)}\n"
        "    save_user(user)\n"
        "    return user\n",
        encoding="utf-8",
    )
    (repo / "helpers.py").write_text(
        "def normalize_name(value: str) -> str:\n"
        "    return value.strip()\n\n"
        "def format_name(value: str) -> str:\n"
        "    return normalize_name(value).title()\n",
        encoding="utf-8",
    )
    (repo / "storage.py").write_text(
        "def save_user(user: dict) -> None:\n    user['saved'] = True\n",
        encoding="utf-8",
    )
    return repo


def _write_corpus(path: Path, tasks: list[dict]) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": "csegraph-context-benchmark-v1",
                "tasks": tasks,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def _perfect_corpus(path: Path) -> Path:
    return _write_corpus(
        path,
        [
            {
                "id": "create-user-pipeline",
                "query": "How does create_user format and save a user?",
                "target": "create_user",
                "expected_nodes": ["symbol::app.py::function::create_user"],
                "expected_files": ["app.py", "helpers.py", "storage.py"],
                "expected_symbols": ["create_user", "format_name", "save_user"],
            },
            {
                "id": "format-name",
                "query": "How is a user name normalized before formatting?",
                "target": "format_name",
                "expected_files": ["helpers.py"],
                "expected_symbols": ["format_name", "normalize_name"],
            },
            {
                "id": "save-user",
                "query": "Where is a user marked as saved?",
                "target": "save_user",
                "expected_files": ["storage.py"],
                "expected_symbols": ["save_user"],
            },
        ],
    )


class TestCountRawTokens:
    def test_counts_all_files(self, tmp_path):
        repo = _make_repo(tmp_path)
        tokens = _count_raw_tokens(repo)
        app_text = (repo / "app.py").read_text(encoding="utf-8")
        helpers_text = (repo / "helpers.py").read_text(encoding="utf-8")
        expected = max(1, math.ceil(len(app_text) / 2.7)) + max(
            1, math.ceil(len(helpers_text) / 2.7)
        )
        assert tokens == expected

    def test_empty_repo(self, tmp_path):
        repo = tmp_path / "empty"
        repo.mkdir()
        assert _count_raw_tokens(repo) == 0


class TestBenchmarkTokenReduction:
    def test_has_token_reduction_step(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = str(_scratch_path(repo, "bench.db"))
        result = BenchmarkService(db).run(
            repo,
            profile="small",
            graph_output_path=_scratch_path(repo, "csegraph-graph.html"),
        )
        step_names = [s.name for s in result.steps]
        assert "token_reduction" in step_names

    def test_token_reduction_stats(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = str(_scratch_path(repo, "bench.db"))
        result = BenchmarkService(db).run(
            repo,
            profile="small",
            graph_output_path=_scratch_path(repo, "csegraph-graph.html"),
        )
        tr = next(s for s in result.steps if s.name == "token_reduction")
        assert "raw_tokens" in tr.stats
        assert "diff_tokens" in tr.stats
        assert "context_tokens" in tr.stats
        assert "reduction_percent" in tr.stats
        assert "naive_to_graph_ratio" in tr.stats
        assert "diff_to_graph_ratio" in tr.stats
        assert "ratio" in tr.stats
        assert tr.stats["raw_tokens"] > 0
        assert isinstance(tr.stats["reduction_percent"], float)
        assert isinstance(tr.stats["naive_to_graph_ratio"], float)
        assert isinstance(tr.stats["diff_to_graph_ratio"], float)

    def test_non_git_repo_reports_zero_diff(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = str(_scratch_path(repo, "bench.db"))
        result = BenchmarkService(db).run(
            repo,
            profile="small",
            graph_output_path=_scratch_path(repo, "csegraph-graph.html"),
        )
        tr = next(s for s in result.steps if s.name == "token_reduction")
        assert tr.stats["diff_tokens"] == 0
        assert tr.stats["diff_to_graph_ratio"] == 0.0

    def test_context_tokens_less_than_raw(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = str(_scratch_path(repo, "bench.db"))
        result = BenchmarkService(db).run(
            repo,
            profile="small",
            graph_output_path=_scratch_path(repo, "csegraph-graph.html"),
        )
        tr = next(s for s in result.steps if s.name == "token_reduction")
        assert tr.stats["context_tokens"] <= tr.stats["raw_tokens"]

    def test_reduction_percent_range(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = str(_scratch_path(repo, "bench.db"))
        result = BenchmarkService(db).run(
            repo,
            profile="small",
            graph_output_path=_scratch_path(repo, "csegraph-graph.html"),
        )
        tr = next(s for s in result.steps if s.name == "token_reduction")
        assert 0.0 <= tr.stats["reduction_percent"] <= 100.0

    def test_json_serializable(self, tmp_path):
        import json

        repo = _make_repo(tmp_path)
        db = str(_scratch_path(repo, "bench.db"))
        result = BenchmarkService(db).run(
            repo,
            profile="small",
            graph_output_path=_scratch_path(repo, "csegraph-graph.html"),
        )
        from csegraph._core.core.models import to_dict

        payload = to_dict(result)
        serialized = json.dumps(payload)
        assert "token_reduction" in serialized
        assert "raw_tokens" in serialized


class TestCountDiffTokens:
    def _git(self, repo: Path, *args: str) -> None:
        subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            env={
                "GIT_AUTHOR_NAME": "t",
                "GIT_AUTHOR_EMAIL": "t@t",
                "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@t",
                "PATH": "/usr/bin:/bin:/usr/local/bin",
            },
        )

    def test_non_git_repo_returns_zero(self, tmp_path):
        repo = _make_repo(tmp_path)
        assert _count_diff_tokens(repo) == 0

    def test_clean_git_repo_returns_zero(self, tmp_path):
        repo = _make_repo(tmp_path)
        self._git(repo, "init", "-q", "-b", "main")
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-q", "-m", "init")
        assert _count_diff_tokens(repo) == 0

    def test_dirty_git_repo_counts_diff(self, tmp_path):
        repo = _make_repo(tmp_path)
        self._git(repo, "init", "-q", "-b", "main")
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-q", "-m", "init")
        (repo / "app.py").write_text(
            'from helpers import fmt\n\ndef greet(name: str) -> str:\n    """Say hello loudly."""\n    return fmt(name).upper()\n',
            encoding="utf-8",
        )
        tokens = _count_diff_tokens(repo)
        assert tokens > 0


class TestBenchmarkContextQuality:
    def test_records_context_contract_and_expected_nodes(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = str(_scratch_path(repo, "bench.db"))
        result = BenchmarkService(db).run(
            repo,
            profile="small",
            graph_output_path=_scratch_path(repo, "csegraph-graph.html"),
            query="Explain greet and fmt",
            target="greet",
            expected_nodes=[
                "symbol::app.py::function::greet",
                "symbol::helpers.py::function::fmt",
            ],
        )

        step_names = [s.name for s in result.steps]
        assert step_names == ["index", "refresh", "context", "graph", "report", "token_reduction"]

        context = next(s for s in result.steps if s.name == "context")
        assert context.stats["schema_version"] == "csegraph-context-v3"
        assert context.stats["detail_level"] == "auto"
        assert context.stats["returned_detail_level"] in {"minimal", "standard"}
        assert context.stats["mcp_response_bytes"] > 0
        assert context.stats["expected_nodes"] == {
            "symbol::app.py::function::greet": True,
            "symbol::helpers.py::function::fmt": True,
        }
        assert context.stats["expected_node_hit_count"] == 2
        assert context.stats["expected_node_total"] == 2
        assert context.stats["expected_node_hit_rate"] == 1.0
        assert context.stats["missing_expected_nodes"] == []

        refresh = next(s for s in result.steps if s.name == "refresh")
        assert refresh.elapsed_ms >= 0
        assert refresh.stats["changed_files"] == 0
        assert refresh.stats["deleted_files"] == 0


class TestBenchmarkCorpusQuality:
    def test_corpus_reports_perfect_quality_metrics(self, tmp_path):
        repo = _make_corpus_repo(tmp_path)
        corpus = _perfect_corpus(tmp_path / "corpus.json")
        db = str(_scratch_path(repo, "corpus.db"))

        result = BenchmarkService(db).run_corpus(repo, corpus, profile="small")

        assert result.command == "benchmark-corpus"
        assert result.corpus_path == str(corpus.resolve())
        assert result.profile == "small"
        assert result.index_stats["files"] == 3
        assert result.index_stats["symbols"] == 4
        assert result.summary.task_count == 3
        assert result.summary.passed_task_count == 3
        assert result.summary.failed_task_count == 0
        assert result.summary.overall_hit_rate == 1.0
        assert result.summary.task_pass_rate == 1.0
        assert result.summary.sufficient_task_count == 3
        assert result.summary.total_tool_call_count == 3
        assert result.summary.total_context_tokens > 0
        assert result.summary.avg_context_tokens > 0
        assert result.summary.total_response_bytes > 0
        assert result.summary.avg_response_bytes > 0

        by_id = {task.task_id: task for task in result.tasks}
        create = by_id["create-user-pipeline"]
        assert create.hit_rate == 1.0
        assert create.expected_node_total == 1
        assert create.file_hit_rate == 1.0
        assert create.symbol_hit_rate == 1.0
        assert create.node_hit_rate == 1.0
        assert create.tool_call_count == 1
        assert create.context_tokens > 0
        assert create.response_bytes > 0
        assert create.returned_node_count >= 3
        assert create.returned_target == "symbol::app.py::function::create_user"
        assert create.returned_detail_level in {"minimal", "standard"}
        assert create.sufficient in {True, False}
        assert create.missing_expected_files == []
        assert create.missing_expected_symbols == []
        assert create.error is None

    def test_corpus_records_partial_misses_without_aborting(self, tmp_path):
        repo = _make_corpus_repo(tmp_path)
        corpus = _write_corpus(
            tmp_path / "corpus.json",
            [
                {
                    "id": "partial",
                    "query": "How does create_user format and save a user?",
                    "target": "create_user",
                    "expected_files": ["app.py", "missing.py"],
                    "expected_symbols": ["create_user", "missing_symbol"],
                }
            ],
        )
        db = str(_scratch_path(repo, "partial.db"))

        result = BenchmarkService(db).run_corpus(repo, corpus, profile="small")

        task = result.tasks[0]
        assert task.hit_rate == 0.5
        assert task.file_hit_rate == 0.5
        assert task.symbol_hit_rate == 0.5
        assert task.missing_expected_files == ["missing.py"]
        assert task.missing_expected_symbols == ["missing_symbol"]
        assert result.summary.task_count == 1
        assert result.summary.passed_task_count == 0
        assert result.summary.failed_task_count == 1
        assert result.summary.overall_hit_rate == 0.5
        assert result.summary.task_pass_rate == 0.0
        assert result.summary.sufficient_task_count in {0, 1}

    def test_corpus_supports_relationship_occurrence_import_and_forbidden_checks(self):
        task = {
            "id": "auth-evidence",
            "query": "How does authenticate_user verify and issue a token?",
            "target": "authenticate_user",
            "expected_symbols": ["authenticate_user", "verify_password"],
            "expected_relationships": [
                {
                    "source": "symbol::auth.py::function::authenticate_user",
                    "relation": "calls",
                    "target": "symbol::passwords.py::function::verify_password",
                }
            ],
            "expected_occurrence_snippets": ["verify_password(password, user['password_hash'])"],
            "expected_import_preludes": ["from passwords import verify_password"],
            "forbidden_source_patterns": ["def verify_password("],
        }

        class _StubService:
            def build_context(self, **_kwargs):
                return ContextResult(
                    command="context",
                    db_path="stub.db",
                    repo_root="/repo",
                    profile="small",
                    query=task["query"],
                    target="symbol::auth.py::function::authenticate_user",
                    detail_level="auto",
                    returned_detail_level="standard",
                    sufficiency=SufficiencyResult(
                        sufficient=True,
                        metrics=SufficiencyMetrics(1.0, 1.0, 1.0, 1.0),
                        thresholds={"dependency_budget": 8.0},
                    ),
                    total_estimated_tokens=42,
                    nodes=[
                        ContextNode(
                            id="symbol::auth.py::function::authenticate_user",
                            kind="function",
                            name="authenticate_user",
                            path="auth.py",
                            line_range=[4, 8],
                            score=1.0,
                            language="python",
                            reason=["target"],
                            reason_details=[],
                            summary="",
                            estimated_tokens=20,
                            source_text=None,
                            source_omitted_reason="source_policy_never",
                        ),
                        ContextNode(
                            id="symbol::passwords.py::function::verify_password",
                            kind="function",
                            name="verify_password",
                            path="passwords.py",
                            line_range=[1, 2],
                            score=0.9,
                            language="python",
                            reason=["direct_call"],
                            reason_details=[],
                            summary="",
                            estimated_tokens=10,
                            source_text=None,
                            source_omitted_reason="source_policy_never",
                        ),
                    ],
                    relationships=[
                        ContextRelationship(
                            source="symbol::auth.py::function::authenticate_user",
                            target="symbol::passwords.py::function::verify_password",
                            relation="calls",
                            metadata={
                                "occurrences": [
                                    {
                                        "path": "auth.py",
                                        "snippet": "verify_password(password, user['password_hash'])",
                                    }
                                ]
                            },
                        )
                    ],
                    import_preludes=[
                        ImportPrelude(
                            path="auth.py",
                            language="python",
                            text="from passwords import verify_password",
                            line_range=[1, 1],
                            source_node_ids=["symbol::auth.py::function::authenticate_user"],
                            resolved_imports=["file::passwords.py"],
                        )
                    ],
                    source_policy="never",
                    raw_code_nodes=[],
                    next_actions=[],
                    warnings=[],
                    run_id=None,
                    confidence_breakdown={},
                    timings_ms={},
                )

        result = _run_corpus_task(
            _StubService(),
            BenchmarkCorpusTask(
                id=task["id"],
                query=task["query"],
                target=task["target"],
                expected_symbols=task["expected_symbols"],
                expected_relationships=task["expected_relationships"],
                expected_occurrence_snippets=task["expected_occurrence_snippets"],
                expected_import_preludes=task["expected_import_preludes"],
                forbidden_source_patterns=task["forbidden_source_patterns"],
            ),
            profile="small",
        )
        assert result.hit_rate == 1.0
        assert result.relationship_hit_rate == 1.0
        assert result.occurrence_snippet_hit_rate == 1.0
        assert result.import_prelude_hit_rate == 1.0
        assert result.forbidden_source_pattern_hit_rate == 1.0
        assert result.missing_expected_relationships == []
        assert result.missing_expected_occurrence_snippets == []
        assert result.missing_expected_import_preludes == []
        assert result.violating_forbidden_source_patterns == []

    @pytest.mark.parametrize(
        ("raw_task", "message"),
        [
            (
                {
                    "id": "bad-relationship",
                    "query": "Explain create_user",
                    "expected_relationships": [{"source": "a", "relation": "calls"}],
                },
                "source, relation, and target",
            ),
        ],
    )
    def test_corpus_validation_rejects_bad_relationship_expectations(self, tmp_path, raw_task, message):
        repo = _make_corpus_repo(tmp_path)
        corpus = tmp_path / "bad-corpus.json"
        corpus.write_text(
            json.dumps({"schema_version": "csegraph-context-benchmark-v1", "tasks": [raw_task]}),
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match=message):
            BenchmarkService(_scratch_path(repo, "bad.db")).run_corpus(repo, corpus)

    @pytest.mark.parametrize(
        ("payload", "message"),
        [
            ({"schema_version": "csegraph-context-benchmark-v1", "tasks": []}, "at least one task"),
            (
                {
                    "schema_version": "csegraph-context-benchmark-v1",
                    "tasks": [
                        {"query": "Explain create_user", "expected_symbols": ["create_user"]}
                    ],
                },
                "non-empty id",
            ),
            (
                {
                    "schema_version": "csegraph-context-benchmark-v1",
                    "tasks": [{"id": "missing-query", "expected_symbols": ["create_user"]}],
                },
                "non-empty query",
            ),
            (
                {
                    "schema_version": "csegraph-context-benchmark-v1",
                    "tasks": [{"id": "missing-expectations", "query": "Explain create_user"}],
                },
                "expected_nodes, expected_files, expected_symbols",
            ),
        ],
    )
    def test_corpus_validation_errors_are_clear(self, tmp_path, payload, message):
        repo = _make_corpus_repo(tmp_path)
        corpus = tmp_path / "bad-corpus.json"
        corpus.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(ValueError, match=message):
            BenchmarkService(_scratch_path(repo, "bad.db")).run_corpus(repo, corpus)

    def test_corpus_result_is_json_serializable(self, tmp_path):
        repo = _make_corpus_repo(tmp_path)
        corpus = _perfect_corpus(tmp_path / "corpus.json")
        db = str(_scratch_path(repo, "serializable.db"))

        result = BenchmarkService(db).run_corpus(repo, corpus, profile="small")
        payload = to_dict(result)
        serialized = json.dumps(payload)

        assert payload["command"] == "benchmark-corpus"
        assert payload["summary"]["task_count"] == 3
        assert "create-user-pipeline" in serialized
