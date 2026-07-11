from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import tools.adaptive_benchmark as adaptive_benchmark
from tools.adaptive_benchmark import (
    AdaptiveBenchmarkCorpus,
    AdaptiveBenchmarkTask,
    BenchmarkRepository,
    PyrightLspProvider,
    StrongBaselineAdapter,
    _fixture_revision,
    _lsp_locations,
    copy_benchmark_repository,
    corpus_completeness,
    corpus_quality,
    execute_benchmark_task,
    load_adaptive_corpus,
    prepare_benchmark_repository,
)
from tools.generate_sandbox_stress_corpus import generate_sandbox_corpora
from tools.run_adaptive_retrieval_benchmark import (
    REPORT_SCHEMA_VERSION,
)
from tools.run_adaptive_retrieval_benchmark import (
    main as run_adaptive_benchmark,
)


def test_benchmark_facade_and_quality_module_share_quality_behavior():
    from tools.benchmarks.quality import corpus_quality as focused_quality

    corpus = adaptive_benchmark.load_adaptive_corpus(
        {
            "schema_version": "csegraph-adaptive-benchmark-v2",
            "corpus_version": "test",
            "tier": "pr",
            "status": "ready",
            "repositories": {},
            "tasks": [],
        }
    )
    assert adaptive_benchmark.corpus_quality(corpus) == focused_quality(corpus)


def test_benchmark_facade_reexports_supported_operations():
    import tools.adaptive_benchmark as adaptive_benchmark
    from tools.benchmarks.execution import execute_benchmark_task
    from tools.benchmarks.workspace import (
        benchmark_workspace_hygiene,
        copy_benchmark_repository,
        prepare_benchmark_repository,
    )

    assert adaptive_benchmark.execute_benchmark_task is execute_benchmark_task
    assert adaptive_benchmark.prepare_benchmark_repository is prepare_benchmark_repository
    assert adaptive_benchmark.copy_benchmark_repository is copy_benchmark_repository
    assert adaptive_benchmark.benchmark_workspace_hygiene is benchmark_workspace_hygiene


def test_benchmark_schema_exposes_validation_boundary():
    from tools.benchmarks.schema import load_corpus, validate_corpus

    repo_root = Path(__file__).resolve().parents[2]
    corpus = load_corpus(
        {
            "schema_version": "csegraph-adaptive-benchmark-v2",
            "corpus_version": "test",
            "tier": "pr",
            "status": "ready",
            "repositories": {},
            "tasks": [],
        }
    )

    validate_corpus(corpus)
    assert corpus.path == Path("<generated>")
    assert repo_root.exists()


def test_strong_baseline_uses_bounded_selective_reads(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "helpers.py").write_text(
        "def clean_name(value: str) -> str:\n    return value.strip().title()\n",
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
        "def greet(name: str) -> str:\n    return f'hello {name}'\n",
        encoding="utf-8",
    )

    class UnavailableProvider:
        warning = "optional language server unavailable " * 20
        last_latency_ms = 0.0
        calls: list[str] = []

        def definitions(self, *_args):
            self.calls.append("definitions")
            return []

        def references(self, *_args):
            self.calls.append("references")
            return []

    provider = UnavailableProvider()
    result = StrongBaselineAdapter(definition_provider=provider).retrieve(
        repo,
        "Explain greet",
        target="greet",
        task_kind="definition",
        token_budget=512,
    )

    assert result.slices
    assert result.slices[0].role == "target"
    assert provider.calls == []
    assert result.usage["lsp_calls"] == 0
    assert result.usage["tokens"] <= 512


def test_strong_baseline_uses_references_for_impact_task(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "def greet(name: str) -> str:\n    return f'hello {name}'\n",
        encoding="utf-8",
    )

    class RecordingProvider:
        warning = None
        last_latency_ms = 0.0

        def __init__(self):
            self.calls: list[str] = []

        def definitions(self, *_args):
            self.calls.append("definitions")
            return []

        def references(self, *_args):
            self.calls.append("references")
            return []

    provider = RecordingProvider()
    result = StrongBaselineAdapter(definition_provider=provider).retrieve(
        repo,
        "Update greet and inspect its callers",
        target="greet",
        task_kind="cross-file",
        token_budget=800,
    )

    assert provider.calls == ["references"]
    assert result.usage["lsp_calls"] == 1


def test_pr_adaptive_corpus_has_balanced_twenty_two_task_v2_fixture():
    repo_root = Path(__file__).resolve().parents[2]
    corpus = adaptive_benchmark.build_adaptive_corpus("pr", repo_root=repo_root)
    tasks = list(corpus.tasks)
    quality = corpus_quality(corpus)

    assert corpus.schema_version == "csegraph-adaptive-benchmark-v2"
    assert len(tasks) == 22
    assert len({task.id for task in tasks}) == 22
    assert all(len(task.commit) == 40 for task in tasks)
    assert sum(task.target is None for task in tasks) >= 8
    assert sum(task.category == "ambiguous" for task in tasks) == 4
    assert sum(task.category == "structural" for task in tasks) == 2
    assert sum(task.execution_mode == "agent" for task in tasks) >= 1
    assert any(
        any(evidence.path.startswith(("test/", "tests/")) for evidence in task.required_evidence)
        for task in tasks
    )
    assert sum(task.expected_status == "insufficient" for task in tasks) >= 1
    assert all(task.permitted_ranges for task in tasks)
    assert all(
        task.expected_target is not None for task in tasks if task.expected_status == "ready"
    )
    assert corpus_completeness(corpus)["complete"] is True
    assert quality["enforced"] is True
    assert quality["gates"]["agent_task_coverage"] is True
    assert quality["gates"]["required_test_evidence"] is True
    assert quality["gates"]["insufficient_budget_coverage"] is True
    assert quality["passed"] is True


def test_source_driven_corpus_builders_replace_tracked_manifests():
    repo_root = Path(__file__).resolve().parents[2]

    assert not list((repo_root / "benchmarks" / "adaptive").glob("*.json"))
    assert hasattr(adaptive_benchmark, "build_adaptive_corpus")
    build_adaptive_corpus = adaptive_benchmark.build_adaptive_corpus

    pr = build_adaptive_corpus("pr", repo_root=repo_root)
    nightly = build_adaptive_corpus("nightly", repo_root=repo_root)
    release = build_adaptive_corpus("release", repo_root=repo_root)

    assert len(pr.tasks) == 22
    assert len(nightly.tasks) == 60
    assert len(release.tasks) == 30
    assert all(task.commit == pr.repositories[task.repo].commit for task in pr.tasks)
    assert corpus_quality(pr)["passed"] is True
    assert corpus_quality(nightly)["passed"] is True
    assert corpus_quality(release)["passed"] is True


def test_named_corpus_can_run_without_a_manifest_file(tmp_path: Path):
    output = tmp_path / "report.json"

    assert (
        run_adaptive_benchmark(
            [
                "--corpus",
                "pr",
                "--repo-root",
                str(Path(__file__).resolve().parents[2]),
                "--limit",
                "0",
                "--pyright",
                "off",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["corpus"]["tier"] == "pr"
    assert report["corpus"]["path"] == "<generated:pr>"


def test_all_corpus_manifests_report_honest_completeness():
    repo_root = Path(__file__).resolve().parents[2]
    pr = adaptive_benchmark.build_adaptive_corpus("pr", repo_root=repo_root)
    nightly = adaptive_benchmark.build_adaptive_corpus("nightly", repo_root=repo_root)

    assert corpus_completeness(pr)["complete"] is True
    assert len(nightly.tasks) == 60
    assert nightly.status == "ready"
    assert all(task.expected_status == "ready" for task in nightly.tasks)
    assert {
        category: sum(task.category == category for task in nightly.tasks)
        for category in {"definition", "debug", "refactor", "cross-file", "test-impact"}
    } == {
        "definition": 12,
        "debug": 12,
        "refactor": 12,
        "cross-file": 12,
        "test-impact": 12,
    }
    assert sum(task.repo == "benchmarks/fixtures/adaptive_pr" for task in nightly.tasks) == 30
    assert sum(task.repo == "benchmarks/fixtures/adaptive_js_ts" for task in nightly.tasks) == 30
    assert corpus_completeness(nightly)["complete"] is True


@pytest.fixture(scope="module")
def generated_sandbox_corpora(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, Path]:
    repo_root = Path(__file__).resolve().parents[2]
    required_repositories = (
        "celery",
        "django",
        "fastapi",
        "flask",
        "micrograd",
        "nanoGPT",
        "pandas",
        "pytest",
        "scikit-learn",
        "transformers",
    )
    missing = [
        name for name in required_repositories if not (repo_root / "sandbox" / name).is_dir()
    ]
    if missing:
        pytest.skip("local sandbox repositories unavailable: " + ", ".join(missing))
    output_root = tmp_path_factory.mktemp("sandbox_corpora") / "benchmarks" / "adaptive"
    return generate_sandbox_corpora(output_root=output_root)


def test_local_pr_fixture_revision_is_content_addressed(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[2]
    corpus = adaptive_benchmark.build_adaptive_corpus("pr", repo_root=repo_root)
    repository = corpus.repositories["benchmarks/fixtures/adaptive_pr"]

    prepared = prepare_benchmark_repository(
        repository,
        repo_root=repo_root,
        cache_root=tmp_path,
        bootstrap_missing=False,
    )

    assert prepared.path == (repo_root / repository.path).resolve()
    assert prepared.observed_commit == repository.commit
    assert prepared.commit_matches is True
    assert prepared.bootstrapped is False


def test_benchmark_repository_copy_scrubs_runtime_artifacts(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.py").write_text("def greet():\n    return 'hi'\n", encoding="utf-8")
    (source / ".git").mkdir()
    (source / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (source / ".csegraph").mkdir()
    (source / ".csegraph" / "index.db").write_bytes(b"sqlite")
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "app.cpython-314.pyc").write_bytes(b"pyc")
    (source / ".pytest_cache").mkdir()
    (source / ".pytest_cache" / "README.md").write_text("cache", encoding="utf-8")
    (source / "build").mkdir()
    (source / "build" / "artifact.txt").write_text("artifact", encoding="utf-8")
    (source / ".DS_Store").write_bytes(b"finder")

    hygiene = copy_benchmark_repository(source, tmp_path / "copy")

    copied = tmp_path / "copy"
    assert (copied / "app.py").is_file()
    assert not (copied / ".git").exists()
    assert not (copied / ".csegraph").exists()
    assert not (copied / "__pycache__").exists()
    assert not (copied / ".pytest_cache").exists()
    assert not (copied / "build").exists()
    assert not (copied / ".DS_Store").exists()
    assert hygiene["clean"] is True
    assert ".csegraph" in hygiene["ignored_names"]


def test_fixture_revision_ignores_same_runtime_artifacts_as_copy(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def greet():\n    return 'hi'\n", encoding="utf-8")
    before = _fixture_revision(repo)

    (repo / ".git").mkdir()
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (repo / ".csegraph").mkdir()
    (repo / ".csegraph" / "index.db").write_bytes(b"sqlite")
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "app.cpython-314.pyc").write_bytes(b"pyc")
    (repo / ".pytest_cache").mkdir()
    (repo / ".pytest_cache" / "README.md").write_text("cache", encoding="utf-8")
    (repo / ".DS_Store").write_bytes(b"finder")

    assert _fixture_revision(repo) == before


def test_corpus_quality_warns_for_flattering_retrieval_corpus(tmp_path: Path):
    task = AdaptiveBenchmarkTask(
        id="exact-only",
        repo="fixture",
        commit="a" * 40,
        category="definition",
        task="Explain greet",
        target="greet",
        expected_status="ready",
        expected_target=None,
        required_evidence=(),
        permitted_ranges=(),
    )
    corpus = AdaptiveBenchmarkCorpus(
        path=tmp_path / "corpus.json",
        schema_version="csegraph-adaptive-benchmark-v2",
        version="test",
        tier="pr",
        status="ready",
        unsupported_reason=None,
        repositories={
            "fixture": BenchmarkRepository(
                path="fixture",
                url="fixture://local",
                commit="a" * 40,
            )
        },
        tasks=(task,),
    )

    quality = corpus_quality(corpus)

    assert quality["gates"]["targetless_coverage"] is False
    assert quality["gates"]["agent_task_coverage"] is False
    assert quality["gates"]["insufficient_budget_coverage"] is False
    assert "all_tasks_have_explicit_targets" in quality["warnings"]
    assert "agent_task_coverage_missing" in quality["warnings"]
    assert quality["passed"] is False


def test_release_corpus_quality_fails_on_serious_retrieval_gaps(tmp_path: Path):
    task = AdaptiveBenchmarkTask(
        id="exact-only",
        repo="fixture",
        commit="a" * 40,
        category="definition",
        task="Explain greet",
        target="greet",
        expected_status="ready",
    )
    corpus = AdaptiveBenchmarkCorpus(
        path=tmp_path / "release.json",
        schema_version="csegraph-adaptive-benchmark-v2",
        version="test",
        tier="release",
        status="ready",
        unsupported_reason=None,
        repositories={
            "fixture": BenchmarkRepository(
                path="fixture",
                url="fixture://local",
                commit="a" * 40,
            )
        },
        tasks=(task,),
    )

    quality = corpus_quality(corpus)

    assert quality["enforced"] is True
    assert quality["passed"] is False
    assert quality["gates"]["targetless_coverage"] is False
    assert quality["gates"]["required_test_evidence"] is False


def test_runner_reports_index_diagnostics_and_adaptive_usage(tmp_path: Path):
    repo = tmp_path / "fixture"
    repo.mkdir()
    (repo / "app.py").write_text(
        "def greet(name: str) -> str:\n    return f'hello {name}'\n",
        encoding="utf-8",
    )
    commit = _fixture_revision(repo)
    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text(
        json.dumps(
            {
                "schema_version": "csegraph-adaptive-benchmark-v2",
                "corpus_version": "diagnostic-test",
                "tier": "pr",
                "status": "ready",
                "repositories": {
                    "fixture": {
                        "url": "fixture://local",
                        "commit": commit,
                    }
                },
                "tasks": [
                    {
                        "id": "greet",
                        "repo": "fixture",
                        "commit": commit,
                        "category": "definition",
                        "task": "Explain greet",
                        "target": "greet",
                        "expected_status": "ready",
                        "expected_target": {
                            "path": "app.py",
                            "line": 1,
                            "name": "greet",
                        },
                        "required_evidence": [{"path": "app.py", "line": 1, "role": "target"}],
                        "permitted_ranges": [{"path": "app.py", "lines": [1, 2]}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "report.json"

    assert (
        run_adaptive_benchmark(
            [
                "--corpus",
                str(corpus_path),
                "--repo-root",
                str(tmp_path),
                "--modes",
                "warm",
                "--samples",
                "2",
                "--pyright",
                "off",
                "--output",
                str(output),
            ]
        )
        == 0
    )

    report = json.loads(output.read_text(encoding="utf-8"))
    repository = report["repositories"]["fixture"]
    task = report["tasks"][0]
    assert REPORT_SCHEMA_VERSION == "csegraph-adaptive-retrieval-report-v4"
    assert repository["workspace_hygiene"]["clean"] is True
    assert repository["index"]["files_indexed"] == 1
    assert repository["index"]["cache_misses"] == 1
    assert repository["index"]["discover_parse_ms"] >= 0
    assert repository["index"]["write_graph_ms"] >= 0
    assert repository["index"]["db_size_bytes"] > 0
    assert repository["index"]["parse_cache_size_bytes"] > 0
    assert report["corpus"]["quality"]["warnings"]
    assert "quality_gates" in report["summary"]
    assert report["configuration"]["samples"] == 2
    assert report["summary"]["by_repo"]["fixture"]["task_count"] == 1
    assert report["summary"]["by_repo"]["fixture"]["adaptive_sample_count"] == 2
    assert task["selected_mode"] == "warm"
    assert task["adaptive"]["sample_count"] == 2
    assert task["adaptive"]["cache"] == "disabled"
    assert task["adaptive"]["engine_latency_ms"] >= 0
    assert task["adaptive"]["tokens"] > 0


def test_local_sandbox_release_corpus_loads_and_has_quality_coverage():
    repo_root = Path(__file__).resolve().parents[2]
    corpus = adaptive_benchmark.build_adaptive_corpus("release", repo_root=repo_root)
    quality = corpus_quality(corpus)

    assert corpus.tier == "release"
    assert len(corpus.tasks) == 30
    assert {
        "sandbox/micrograd",
        "sandbox/flask",
        "sandbox/django",
    }.issubset(corpus.repositories)
    assert corpus_completeness(corpus)["complete"] is True
    assert quality["gates"]["targetless_coverage"] is True
    assert quality["gates"]["ambiguous_coverage"] is True
    assert quality["gates"]["structural_followup_coverage"] is True
    assert quality["gates"]["insufficient_budget_coverage"] is True
    assert quality["gates"]["required_test_evidence"] is True
    assert quality["passed"] is True


def test_local_sandbox_stress_corpus_loads_and_has_perf_coverage(
    generated_sandbox_corpora: dict[str, Path],
):
    corpus = load_adaptive_corpus(generated_sandbox_corpora["stress"])
    quality = corpus_quality(corpus)

    assert corpus.tier == "perf"
    assert len(corpus.tasks) == 220
    assert {
        repo: sum(task.repo == repo for task in corpus.tasks)
        for repo in {"sandbox/micrograd", "sandbox/flask", "sandbox/django"}
    } == {
        "sandbox/micrograd": 20,
        "sandbox/flask": 100,
        "sandbox/django": 100,
    }
    assert corpus_completeness(corpus)["complete"] is True
    assert quality["gates"]["targetless_coverage"] is True
    assert quality["gates"]["ambiguous_coverage"] is True
    assert quality["gates"]["structural_followup_coverage"] is True
    assert quality["gates"]["insufficient_budget_coverage"] is True
    assert quality["gates"]["required_test_evidence"] is True
    assert quality["gates"]["exact_target_ratio_at_most_90pct"] is False
    assert "exact_target_ratio_at_most_90pct" not in quality["enforced_gate_names"]
    assert quality["passed"] is True


def test_local_sandbox_broad_corpus_covers_all_local_sandboxes(
    generated_sandbox_corpora: dict[str, Path],
):
    corpus = load_adaptive_corpus(generated_sandbox_corpora["broad"])
    quality = corpus_quality(corpus)

    expected_counts = {
        "sandbox/celery": 40,
        "sandbox/django": 40,
        "sandbox/fastapi": 40,
        "sandbox/flask": 40,
        "sandbox/micrograd": 20,
        "sandbox/nanoGPT": 8,
        "sandbox/pandas": 40,
        "sandbox/pytest": 40,
        "sandbox/scikit-learn": 40,
        "sandbox/transformers": 40,
    }

    assert corpus.tier == "broad"
    assert len(corpus.tasks) == sum(expected_counts.values())
    assert set(corpus.repositories) == set(expected_counts)
    assert {
        repo: sum(task.repo == repo for task in corpus.tasks) for repo in expected_counts
    } == expected_counts
    assert corpus_completeness(corpus)["complete"] is True
    assert quality["gates"]["targetless_coverage"] is True
    assert quality["gates"]["ambiguous_coverage"] is True
    assert quality["gates"]["structural_followup_coverage"] is True
    assert quality["gates"]["insufficient_budget_coverage"] is True
    assert quality["gates"]["required_test_evidence"] is True
    assert quality["gates"]["exact_target_ratio_at_most_90pct"] is False
    assert "exact_target_ratio_at_most_90pct" not in quality["enforced_gate_names"]
    assert quality["passed"] is True


@pytest.mark.parametrize("repo_path", ["", ".", "./", "csegraph", "./csegraph", "csegraph/src"])
def test_adaptive_corpus_loader_rejects_csegraph_self_repository(
    tmp_path: Path,
    repo_path: str,
):
    corpus = {
        "schema_version": "csegraph-adaptive-benchmark-v2",
        "corpus_version": "test",
        "tier": "pr",
        "status": "ready",
        "repositories": {
            repo_path: {
                "url": "fixture://local",
                "commit": "a" * 40,
            }
        },
        "tasks": [
            {
                "id": "self",
                "repo": repo_path,
                "commit": "a" * 40,
                "category": "definition",
                "task": "Explain ContextService",
                "expected_status": "ready",
                "expected_target": {
                    "id": "symbol::csegraph/_core/retrieval/adaptive.py::class::ContextService",
                    "path": "csegraph/_core/retrieval/adaptive.py",
                    "line": 57,
                },
                "required_evidence": [
                    {
                        "path": "csegraph/_core/retrieval/adaptive.py",
                        "line": 57,
                        "role": "target",
                    }
                ],
                "permitted_ranges": [
                    {
                        "path": "csegraph/_core/retrieval/adaptive.py",
                        "lines": [57, 66],
                    }
                ],
            }
        ],
    }
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(corpus), encoding="utf-8")

    with pytest.raises(ValueError, match="CseGraph itself"):
        load_adaptive_corpus(path)


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


def test_v2_corpus_loader_rejects_file_only_expectations(tmp_path: Path):
    corpus = {
        "schema_version": "csegraph-adaptive-benchmark-v2",
        "corpus_version": "test",
        "tier": "pr",
        "status": "ready",
        "repositories": {
            "fixture": {
                "url": "fixture://local",
                "commit": "a" * 40,
            }
        },
        "tasks": [
            {
                "id": "file-only",
                "repo": "fixture",
                "commit": "a" * 40,
                "category": "definition",
                "task": "Explain greet",
                "expected_status": "ready",
                "expected_locations": ["app.py"],
                "permitted_files": ["app.py"],
            }
        ],
    }
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(corpus), encoding="utf-8")

    with pytest.raises(ValueError, match="expected_target"):
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
        "class Attention:\n    def forward(self, value):\n        return value\n",
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
        "tools.benchmarks.baseline.shutil.which",
        lambda command: f"/bin/{command}",
    )
    monkeypatch.setattr(
        "tools.benchmarks.baseline.subprocess.run",
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
    (source / "app.py").write_text(
        "def hello():\n    return 'hello'\n",
        encoding="utf-8",
    )
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
            ("from pathlib import Path; assert 'VALUE = 2' in Path('app.py').read_text()"),
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
