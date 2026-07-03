from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from csegraph._core.benchmark_baseline import (
    AdaptiveBenchmarkTask,
    BenchmarkRepository,
    PyrightLspProvider,
    StrongBaselineAdapter,
    _lsp_locations,
    corpus_completeness,
    execute_benchmark_task,
    load_adaptive_corpus,
    load_adaptive_tasks,
    prepare_benchmark_repository,
)


def test_strong_baseline_uses_bounded_selective_reads(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "helpers.py").write_text(
        "def clean_name(value: str) -> str:\n"
        "    return value.strip().title()\n",
        encoding="utf-8",
    )
    (repo / "users.py").write_text(
        "from helpers import clean_name\n\n"
        "def create_user(name: str) -> dict[str, str]:\n"
        "    return {'name': clean_name(name)}\n",
        encoding="utf-8",
    )

    result = StrongBaselineAdapter().retrieve(
        repo,
        "Implement create_user with clean_name",
        target="create_user",
        token_budget=800,
    )

    assert result.slices
    assert result.slices[0].path == "users.py"
    assert any(item.path == "helpers.py" for item in result.slices)
    assert result.usage["tokens"] <= 800
    assert all(item.lines[1] - item.lines[0] + 1 <= 80 for item in result.slices)


def test_strong_baseline_final_metadata_remains_inside_budget(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "def greet(name: str) -> str:\n"
        "    return f'hello {name}'\n",
        encoding="utf-8",
    )

    class UnavailableProvider:
        warning = "optional language server unavailable " * 20
        last_latency_ms = 0.0

        def definitions(self, *_args):
            return []

        def references(self, *_args):
            return []

    result = StrongBaselineAdapter(
        definition_provider=UnavailableProvider()
    ).retrieve(
        repo,
        "Explain greet",
        target="greet",
        token_budget=512,
    )

    assert result.slices
    assert result.slices[0].role == "target"
    assert result.usage["tokens"] <= 512


def test_pr_adaptive_corpus_has_twenty_versioned_tasks():
    repo_root = Path(__file__).resolve().parents[2]
    tasks = load_adaptive_tasks(repo_root / "benchmarks" / "adaptive" / "pr_tasks.json")

    assert len(tasks) == 20
    assert len({task.id for task in tasks}) == 20
    assert all(len(task.commit) == 40 for task in tasks)
    assert all(task.expected_locations for task in tasks)


def test_all_corpus_manifests_report_honest_completeness():
    repo_root = Path(__file__).resolve().parents[2]
    pr = load_adaptive_corpus(repo_root / "benchmarks/adaptive/pr_tasks.json")
    nightly = load_adaptive_corpus(repo_root / "benchmarks/adaptive/nightly_tasks.json")
    release = load_adaptive_corpus(repo_root / "benchmarks/adaptive/release_tasks.json")

    assert corpus_completeness(pr)["complete"] is True
    assert len(nightly.tasks) == 60
    assert corpus_completeness(nightly)["complete"] is True
    assert release.status == "blocked"
    assert release.unsupported_reason
    assert corpus_completeness(release)["complete"] is False


def test_corpus_loader_rejects_duplicate_task_ids(tmp_path: Path):
    corpus = {
        "schema_version": "csegraph-adaptive-benchmark-v1",
        "corpus_version": "test",
        "tier": "pr",
        "status": "ready",
        "repositories": {
            "repo": {
                "url": "https://example.invalid/repo.git",
                "commit": "a" * 40,
            }
        },
        "tasks": [
            {
                "id": "same",
                "repo": "repo",
                "commit": "a" * 40,
                "category": "definition",
                "task": "one",
            },
            {
                "id": "same",
                "repo": "repo",
                "commit": "a" * 40,
                "category": "definition",
                "task": "two",
            },
        ],
    }
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(corpus), encoding="utf-8")

    with pytest.raises(ValueError, match="Duplicate benchmark task id"):
        load_adaptive_corpus(path)


def test_strong_baseline_normalizes_explicit_symbol_ids(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    docs = repo / "docs"
    docs.mkdir()
    (docs / "attention.md").write_text(
        "Attention forward implementation and examples.\n",
        encoding="utf-8",
    )
    (repo / "model.py").write_text(
        "class Attention:\n"
        "    def forward(self, value):\n"
        "        return value\n",
        encoding="utf-8",
    )

    result = StrongBaselineAdapter().retrieve(
        repo,
        "Explain the attention forward implementation",
        target="symbol::model.py::method::Attention.forward",
        token_budget=800,
    )

    assert result.slices
    assert result.slices[0].path == "model.py"
    assert result.discovery
    assert result.usage["tokens"] <= 800
    assert result.usage["rg_calls"] == 1
    assert result.usage["file_read_calls"] >= 1


def test_lsp_locations_accepts_location_and_location_link():
    locations = _lsp_locations(
        [
            {
                "uri": "file:///tmp/example.py",
                "range": {"start": {"line": 4, "character": 2}},
            },
            {
                "targetUri": "file:///tmp/linked.py",
                "targetSelectionRange": {"start": {"line": 8, "character": 1}},
            },
        ]
    )

    assert locations == [(Path("/tmp/example.py"), 5), (Path("/tmp/linked.py"), 9)]


def test_pyright_provider_disables_version_mismatch(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "csegraph._core.benchmark_baseline.shutil.which",
        lambda command: f"/bin/{command}",
    )
    monkeypatch.setattr(
        "csegraph._core.benchmark_baseline.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="pyright 0.0.1\n",
            stderr="",
        ),
    )

    provider = PyrightLspProvider(expected_version="1.1.407")

    assert provider.available is False
    assert "expected version 1.1.407" in str(provider.warning)


def test_repository_bootstrap_uses_pinned_local_commit(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(
        ["git", "-C", str(source), "config", "user.email", "benchmark@example.test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(source), "config", "user.name", "Benchmark"],
        check=True,
    )
    (source / "app.py").write_text("def hello():\n    return 'hello'\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "app.py"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-qm", "fixture"], check=True)
    commit = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    repository = BenchmarkRepository(
        path="missing/repo",
        url=str(source),
        commit=commit,
    )

    prepared = prepare_benchmark_repository(
        repository,
        repo_root=tmp_path / "workspace",
        cache_root=tmp_path / "cache",
        bootstrap_missing=True,
    )

    assert prepared.path is not None
    assert prepared.bootstrapped is True
    assert prepared.commit_matches is True
    assert prepared.observed_commit == commit


def test_agent_task_executor_runs_argv_checks_and_enforces_permitted_files(
    tmp_path: Path,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "benchmark@example.test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Benchmark"],
        check=True,
    )
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "app.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fixture"], check=True)
    task = AdaptiveBenchmarkTask(
        id="agent-fixture",
        repo="repo",
        commit="a" * 40,
        category="debug",
        task="Set value to two",
        expected_locations=("app.py",),
        permitted_files=("app.py",),
        test_command=(
            sys.executable,
            "-c",
            "from pathlib import Path; assert 'VALUE = 2' in Path('app.py').read_text()",
        ),
        hidden_checks=(
            (
                sys.executable,
                "-c",
                "from pathlib import Path; assert Path('app.py').exists()",
            ),
        ),
        execution_mode="agent",
    )

    result = execute_benchmark_task(
        task,
        repo,
        agent_command=(
            sys.executable,
            "-c",
            "from pathlib import Path; Path('app.py').write_text('VALUE = 2\\n')",
        ),
    )

    assert result.status == "passed"
    assert result.changed_files == ("app.py",)
    assert result.permitted_files_ok is True
