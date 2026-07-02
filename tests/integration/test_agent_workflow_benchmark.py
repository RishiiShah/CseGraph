"""Agent workflow benchmarks exercise the MCP context loop end-to-end."""

from __future__ import annotations

from pathlib import Path

from csegraph._core.benchmark import BenchmarkService


def _write_tiny_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    (repo / "app.py").write_text(
        "def greet():\n    return 'hi'\n\ndef main():\n    greet()\n",
        encoding="utf-8",
    )


def test_agent_workflow_benchmark_respects_tool_budget(tmp_path):
    repo = tmp_path / "repo"
    _write_tiny_repo(repo)
    db = repo / ".scratch" / "csegraph" / "bench.db"

    result = BenchmarkService(str(db)).run_agent_workflows(repo, profile="small")
    assert result.command == "benchmark-agent-workflows"

    summaries = [step for step in result.steps if step.name.endswith(":summary")]
    assert len(summaries) == 3
    for step in summaries:
        stats = step.stats
        assert stats["tool_calls"] <= stats["max_tool_calls"]
        assert stats["within_turn_budget"] is True
        assert stats["total_estimated_tokens"] > 0
        assert "edit_ready" in stats
        assert "tool_calls_to_edit_ready" in stats
        assert "tokens_to_edit_ready" in stats
        assert "missing_context_count" in stats

        if stats["edit_ready"]:
            assert 1 <= stats["tool_calls_to_edit_ready"] <= stats["tool_calls"]
            assert 0 < stats["tokens_to_edit_ready"] <= stats["total_estimated_tokens"]
