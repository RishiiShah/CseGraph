from __future__ import annotations

from pathlib import Path

from csegraph._core.benchmark_baseline import (
    StrongBaselineAdapter,
    load_adaptive_tasks,
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


def test_pr_adaptive_corpus_has_twenty_versioned_tasks():
    repo_root = Path(__file__).resolve().parents[2]
    tasks = load_adaptive_tasks(repo_root / "benchmarks" / "adaptive" / "pr_tasks.json")

    assert len(tasks) == 20
    assert len({task.id for task in tasks}) == 20
    assert all(len(task.commit) == 40 for task in tasks)
    assert all(task.expected_locations for task in tasks)
