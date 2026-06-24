import json
import os
import re
import site
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import csegraph._cli.main as cli_main
from csegraph._core.retrieval.constants import VALID_REASONS
from tests.conftest import run_cli, run_dev_cli


def _write_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "helpers.py").write_text(
        "def clean_name(value: str) -> str:\n    return value.strip().lower()\n",
        encoding="utf-8",
    )
    (root / "service.py").write_text(
        "from helpers import clean_name\n\ndef create_user(name: str) -> dict:\n    return {'name': clean_name(name)}\n",
        encoding="utf-8",
    )


def _offline_pip_env() -> dict:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    return env


def _create_test_venv(path: Path) -> None:
    subprocess.run([sys.executable, "-m", "venv", str(path)], check=True)
    child_site_packages = Path(
        subprocess.check_output(
            [
                str(
                    path
                    / ("Scripts" if sys.platform.startswith("win") else "bin")
                    / ("python.exe" if sys.platform.startswith("win") else "python")
                ),
                "-c",
                "import site; print(site.getsitepackages()[0])",
            ],
            text=True,
        ).strip()
    )
    parent_site_packages = Path(site.getsitepackages()[0])
    excluded_prefixes = (
        "pip",
        "csegraph",
        "csegraph_",
        "__editable__.csegraph",
        "__editable___csegraph",
    )
    for entry in parent_site_packages.iterdir():
        if entry.name.startswith(excluded_prefixes):
            continue
        target = child_site_packages / entry.name
        if target.exists():
            continue
        target.symlink_to(entry)


def test_cli_profile_choices_accept_auto():
    parser = cli_main._build_parser()

    args = parser.parse_args(["index", "/tmp/repo", "--profile", "auto"])

    assert args.profile == "auto"


def test_cli_json_contracts(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)

    indexed = run_cli(
        "index",
        str(repo),
        "--json",
    )
    assert indexed["command"] == "index"
    assert indexed["profile"] == "medium"
    assert indexed["files_indexed"] == 2
    assert indexed["symbols_indexed"] == 2
    assert indexed["db_path"] == str(repo / ".csegraph" / "index.db")

    context = run_cli(
        "context",
        "Implement create_user with clean_name",
        "--target",
        "create_user",
        "--repo",
        str(repo),
        "--json",
    )
    assert context["command"] == "context"
    assert context["schema_version"] == "csegraph-context-v3"
    assert context["request"]["task"] == "Implement create_user with clean_name"
    assert context["target"]["id"] == "symbol::service.py::function::create_user"
    assert context["request"]["detail_level"] == "auto"
    assert context["request"]["returned_detail_level"] == "minimal"
    assert context["sufficiency"]["sufficient"] is True
    assert "target_node_id" not in context
    assert "context_nodes" not in context
    assert "nodes" not in context
    assert "estimated_tokens" not in context
    assert "metrics" not in context
    assert "thresholds" not in context
    assert "is_sufficient" not in context
    assert any(
        node["id"] == "symbol::helpers.py::function::clean_name" for node in context["symbols"]
    )
    assert context["budgets"]["total_estimated_tokens"] >= 1
    canonical_by_id = {node["id"]: node for node in context["symbols"]}
    target_node = canonical_by_id["symbol::service.py::function::create_user"]
    helper_node = canonical_by_id["symbol::helpers.py::function::clean_name"]
    assert target_node["path"] == "service.py"
    assert target_node["line_range"] == [3, 4]
    assert "target" in target_node["reason"]
    if "symbol::helpers.py::function::clean_name" in canonical_by_id:
        assert "direct_call" in helper_node["reason"]
    assert all(reason in VALID_REASONS for node in context["symbols"] for reason in node["reason"])
    assert all(
        "expanded-from-" not in reason for node in context["symbols"] for reason in node["reason"]
    )
    assert all("explanation" not in node for node in context["symbols"])
    assert "source_text" not in target_node
    assert target_node["estimated_tokens"] >= 1
    assert any(action["action"] == "expand_context" for action in context["next_actions"])

    standard_context = run_cli(
        "context",
        "Implement create_user with clean_name",
        "--target",
        "create_user",
        "--repo",
        str(repo),
        "--detail-level",
        "standard",
        "--json",
    )
    standard_by_id = {node["id"]: node for node in standard_context["symbols"]}
    assert standard_context["request"]["returned_detail_level"] == "standard"
    assert (
        "def create_user(name: str) -> dict:"
        in standard_by_id["symbol::service.py::function::create_user"]["source_text"]
    )
    assert (
        "def clean_name(value: str) -> str:"
        in standard_by_id["symbol::helpers.py::function::clean_name"]["source_text"]
    )

    neighborhood = run_cli(
        "inspect",
        "symbol::service.py::function::create_user",
        "--repo",
        str(repo),
        "--depth",
        "1",
        "--detail-level",
        "standard",
        "--json",
    )
    assert neighborhood["command"] == "inspect"
    assert neighborhood["target"] == "symbol::service.py::function::create_user"
    assert any(edge["relation"] == "calls" for edge in neighborhood["edges"])

    graph = run_cli(
        "export",
        "--repo",
        str(repo),
        "--format",
        "html",
        "--json",
    )
    assert graph["command"] == "export"
    assert graph["format"] == "html"
    assert graph["output_path"] == str(repo / ".csegraph" / "csegraph-graph.html")

    refreshed = run_cli(
        "refresh",
        str(repo),
        "--json",
    )
    assert refreshed["command"] == "refresh"
    assert refreshed["changed_files"] == []
    assert refreshed["deleted_files"] == []


def test_index_default_output_is_human_summary(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)

    proc = subprocess.run(
        [sys.executable, "-m", "csegraph._cli", "index", str(repo)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert proc.stdout.startswith("Parsing: 2 files")
    assert "Indexing:" in proc.stdout
    assert "Postprocess:" in proc.stdout
    assert "Full index: 2 files," in proc.stdout
    assert "symbols" in proc.stdout
    assert "edges" in proc.stdout
    assert "postprocess=full" in proc.stdout
    assert "FTS" in proc.stdout
    assert "inferred edges" in proc.stdout
    assert "communities" in proc.stdout
    assert "Cache:" in proc.stdout
    assert "Profile: medium" in proc.stdout
    assert "DB: .csegraph/index.db" in proc.stdout


def test_refresh_default_output_is_human_summary(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    run_cli("index", str(repo), "--json")

    proc = subprocess.run(
        [sys.executable, "-m", "csegraph._cli", "refresh", str(repo)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert proc.stdout.startswith("Scanning:")
    assert "0 changed" in proc.stdout
    assert "2 unchanged" in proc.stdout
    assert "Postprocess: skipped" in proc.stdout
    assert "Refresh:" in proc.stdout
    assert "postprocess=skipped" in proc.stdout
    assert "Cache:" in proc.stdout
    assert "Profile: medium" in proc.stdout
    assert "DB: .csegraph/index.db" in proc.stdout


def test_index_json_flag_returns_parseable_json(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)

    result = run_cli("index", str(repo), "--json")

    assert result["command"] == "index"
    assert result["files_indexed"] == 2
    assert result["cache_hits"] == 0
    assert result["cache_misses"] == 2
    assert result["postprocess_level"] == "full"
    assert result["postprocess"]["level"] == "full"
    assert result["graph_totals"]["files"] == 2
    assert result["graph_totals"]["nodes"] >= result["files_indexed"]
    assert result["graph_totals"]["edges"] >= result["edges_indexed"]
    assert isinstance(result["changed_files"], list)


def test_index_include_root_cli_limits_monorepo_subtree(tmp_path):
    repo = tmp_path / "repo"
    api = repo / "apps" / "api"
    web = repo / "apps" / "web"
    api.mkdir(parents=True)
    web.mkdir(parents=True)
    (api / "service.py").write_text("def api_handler():\n    return 'api'\n", encoding="utf-8")
    (web / "view.py").write_text("def web_view():\n    return 'web'\n", encoding="utf-8")

    result = run_cli("index", str(repo), "--include-root", "apps/api", "--json")

    assert result["files_indexed"] == 1
    assert result["changed_files"] == ["apps/api/service.py"]


def test_refresh_json_flag_returns_parseable_json(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    run_cli("index", str(repo), "--json")

    result = run_cli("refresh", str(repo), "--json")

    assert result["command"] == "refresh"
    assert result["cache_hits"] == 2
    assert result["cache_misses"] == 0
    assert result["postprocess_level"] == "full"
    assert result["postprocess_skipped_reason"] == "unchanged"
    assert result["graph_totals"]["files"] == 2
    assert isinstance(result["unchanged_files"], list)


def test_context_detail_level_full_adds_explanations(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    run_cli("index", str(repo), "--json")

    result = run_cli(
        "context",
        "Implement create_user with clean_name",
        "--target",
        "create_user",
        "--repo",
        str(repo),
        "--detail-level",
        "full",
        "--json",
    )

    assert result["request"]["detail_level"] == "full"
    assert result["request"]["returned_detail_level"] == "full"
    assert any("explanation" in node for node in result["symbols"])
    assert all("source_text" in node for node in result["symbols"])


def test_help_lists_canonical_index_and_refresh_without_aliases():
    proc = subprocess.run(
        [sys.executable, "-m", "csegraph._cli", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "index" in proc.stdout
    assert "refresh" in proc.stdout
    assert ("Alias for " + "index") not in proc.stdout
    assert ("Alias for " + "refresh") not in proc.stdout


def test_build_and_update_are_not_public_commands():
    for command in ("build", "update"):
        proc = subprocess.run(
            [sys.executable, "-m", "csegraph._cli", command, "--help"],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 2
        assert "invalid choice" in proc.stderr


def test_build_parser_exposes_only_index_and_refresh_commands(tmp_path):
    parser = cli_main._build_parser()

    index_args = parser.parse_args(["index", str(tmp_path), "--json"])
    refresh_args = parser.parse_args(["refresh", str(tmp_path), "--json"])

    assert index_args.command == "index"
    assert refresh_args.command == "refresh"
    with pytest.raises(SystemExit):
        parser.parse_args(["build", str(tmp_path)])
    with pytest.raises(SystemExit):
        parser.parse_args(["update", str(tmp_path)])


def test_dispatch_keeps_index_and_refresh_canonical_commands(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    parser = cli_main._build_parser()

    indexed = cli_main._dispatch(parser.parse_args(["index", str(repo), "--json"]))
    refreshed = cli_main._dispatch(parser.parse_args(["refresh", str(repo), "--json"]))

    assert indexed.command == "index"
    assert refreshed.command == "refresh"


def test_main_renders_index_json_for_canonical_command(tmp_path, capsys):
    repo = tmp_path / "repo"
    _write_repo(repo)

    assert cli_main.main(["index", str(repo), "--json"]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["command"] == "index"
    assert output["files_indexed"] == 2


def test_install_dry_run_json_reports_auto_targets(tmp_path):
    cli = tmp_path / "env" / "bin" / "csegraph"
    cli.parent.mkdir(parents=True)
    cli.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(cli, 0o755)
    result = run_cli("install", str(tmp_path), "--dry-run", "--json")

    assert result["command"] == "install"
    assert result["dry_run"] is True
    assert result["server_command"] == str(cli.resolve())
    assert result["server_args"] == ["serve", "--repo", str(tmp_path.resolve())]
    assert result["verification"]["state"] == "skipped"
    assert {target["platform"] for target in result["installed"]} == {
        "codex",
        "claude-code",
        "cursor",
        "gemini-cli",
        "kiro",
        "antigravity-cli",
        "copilot",
        "instructions",
        "hooks:claude-code",
        "hooks:codex",
        "gitignore",
    }


def test_install_cursor_dry_run_json_uses_cursor_config(tmp_path):
    result = run_cli(
        "install",
        str(tmp_path),
        "--platform",
        "cursor",
        "--dry-run",
        "--json",
    )

    assert result["installed"][0]["platform"] == "cursor"
    assert result["installed"][0]["path"].endswith(os.path.join(".cursor", "mcp.json"))


def test_doctor_auto_json_reports_project_platforms(tmp_path):
    cli = tmp_path / "env" / "bin" / "csegraph"
    cli.parent.mkdir(parents=True)
    cli.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(cli, 0o755)

    result = run_cli("doctor", str(tmp_path), "--platform", "auto", "--no-verify", "--json")

    assert result["command"] == "doctor"
    assert result["platform"] == "auto"
    assert result["state"] == "config_missing"
    assert result["configured_count"] == 0
    assert result["missing_count"] == 8
    assert result["contract_invalid_count"] == 0
    assert {platform["platform"] for platform in result["platforms"]} == {
        "codex",
        "claude-code",
        "cursor",
        "gemini-cli",
        "kiro",
        "antigravity-cli",
        "copilot",
        "vscode",
    }


def test_install_codex_dry_run_json_uses_repo_config(tmp_path):
    result = run_cli(
        "install",
        str(tmp_path),
        "--platform",
        "codex",
        "--dry-run",
        "--json",
    )

    assert result["installed"][0]["platform"] == "codex"
    assert result["installed"][0]["path"].endswith(os.path.join(".codex", "config.toml"))
    assert result["installed"][0]["scope"] == "project"
    assert {target["platform"] for target in result["installed"]} >= {
        "codex",
        "instructions",
        "hooks:codex",
        "gitignore",
    }


def test_install_codex_no_hooks_skips_hook_targets(tmp_path):
    result = run_cli(
        "install",
        str(tmp_path),
        "--platform",
        "codex",
        "--no-hooks",
        "--dry-run",
        "--json",
    )

    assert "hooks:codex" not in {target["platform"] for target in result["installed"]}


def test_install_codex_no_gitignore_skips_gitignore_target(tmp_path):
    result = run_cli(
        "install",
        str(tmp_path),
        "--platform",
        "codex",
        "--no-gitignore",
        "--dry-run",
        "--json",
    )

    assert "gitignore" not in {target["platform"] for target in result["installed"]}


def test_benchmark_json_profiles_core_commands(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)

    result = run_dev_cli(
        "benchmark",
        str(repo),
        "--target",
        "create_user",
        "--query",
        "Implement create_user with clean_name",
        "--expect-node",
        "symbol::service.py::function::create_user",
        "--expect-node",
        "symbol::helpers.py::function::clean_name",
        "--json",
    )

    assert result["command"] == "benchmark"
    assert result["profile"] == "medium"
    assert result["repo_root"] == str(repo)
    assert result["db_path"] == str(repo / ".csegraph" / "index.db")
    assert result["graph_output_path"] == str(repo / ".csegraph" / "csegraph-graph.html")
    assert result["total_elapsed_ms"] >= 0

    steps = result["steps"]
    assert [step["name"] for step in steps] == [
        "index",
        "refresh",
        "context",
        "graph",
        "report",
        "token_reduction",
    ]
    assert all(step["elapsed_ms"] >= 0 for step in steps)

    by_name = {step["name"]: step for step in steps}
    assert by_name["index"]["stats"]["files"] == 2
    assert by_name["index"]["stats"]["symbols"] == 2
    assert by_name["index"]["stats"]["edges"] >= 1
    assert set(by_name["index"]["stats"]["phases"]) == {
        "discover_parse",
        "initialize_schema",
        "clear_graph",
        "write_graph",
        "parse_errors",
    }
    assert all(elapsed_ms >= 0 for elapsed_ms in by_name["index"]["stats"]["phases"].values())
    assert by_name["refresh"]["stats"]["changed_files"] == 0
    assert by_name["refresh"]["stats"]["deleted_files"] == 0
    assert by_name["context"]["stats"]["nodes"] >= 1
    assert by_name["context"]["stats"]["target"] == "symbol::service.py::function::create_user"
    assert by_name["context"]["stats"]["schema_version"] == "csegraph-context-v3"
    assert by_name["context"]["stats"]["returned_detail_level"] in {"minimal", "standard"}
    assert by_name["context"]["stats"]["mcp_response_bytes"] > 0
    assert by_name["context"]["stats"]["expected_node_hit_count"] == 2
    assert by_name["context"]["stats"]["expected_node_total"] == 2
    assert by_name["context"]["stats"]["expected_node_hit_rate"] == 1.0
    assert by_name["context"]["stats"]["missing_expected_nodes"] == []
    assert by_name["graph"]["stats"]["nodes"] >= 1
    assert by_name["graph"]["stats"]["edges"] >= 1
    assert by_name["graph"]["stats"]["output_size_bytes"] > 0
    assert by_name["report"]["stats"]["files"] == 2
    assert by_name["report"]["stats"]["symbols"] == 2
    assert (repo / ".csegraph" / "csegraph-graph.html").exists()


def test_benchmark_default_output_is_human_summary(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)

    proc = subprocess.run(
        [
            sys.executable,
            "tools/csegraph_dev.py",
            "benchmark",
            str(repo),
            "--target",
            "create_user",
            "--expect-node",
            "symbol::service.py::function::create_user",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Benchmark:" in proc.stdout
    assert "index" in proc.stdout
    assert "context" in proc.stdout
    assert "graph" in proc.stdout
    assert "report" in proc.stdout
    assert "expected_nodes=1/1" in proc.stdout
    assert "hit_rate=100.0%" in proc.stdout
    assert "Total:" in proc.stdout
    assert "DB:" in proc.stdout


def test_benchmark_corpus_json_reports_quality_scoreboard(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    corpus = tmp_path / "corpus.json"
    corpus.write_text(
        json.dumps(
            {
                "schema_version": "csegraph-context-benchmark-v1",
                "tasks": [
                    {
                        "id": "create-user",
                        "query": "How does create_user clean a name?",
                        "target": "create_user",
                        "expected_files": ["service.py", "helpers.py"],
                        "expected_symbols": ["create_user", "clean_name"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_dev_cli(
        "benchmark",
        str(repo),
        "--corpus",
        str(corpus),
        "--json",
    )

    assert result["command"] == "benchmark-corpus"
    assert result["corpus_path"] == str(corpus.resolve())
    assert result["summary"]["task_count"] == 1
    assert result["summary"]["passed_task_count"] == 1
    assert result["summary"]["overall_hit_rate"] == 1.0
    assert result["summary"]["sufficient_task_count"] == 1
    assert result["summary"]["total_tool_call_count"] == 1
    assert result["tasks"][0]["task_id"] == "create-user"
    assert result["tasks"][0]["hit_rate"] == 1.0
    assert result["tasks"][0]["context_tokens"] > 0
    assert result["tasks"][0]["response_bytes"] > 0


def test_benchmark_corpus_default_output_is_human_summary(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    corpus = tmp_path / "corpus.json"
    corpus.write_text(
        json.dumps(
            {
                "schema_version": "csegraph-context-benchmark-v1",
                "tasks": [
                    {
                        "id": "create-user",
                        "query": "How does create_user clean a name?",
                        "target": "create_user",
                        "expected_files": ["service.py", "helpers.py"],
                        "expected_symbols": ["create_user", "clean_name"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            "tools/csegraph_dev.py",
            "benchmark",
            str(repo),
            "--corpus",
            str(corpus),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Context Quality Benchmark:" in proc.stdout
    assert "Tasks: 1" in proc.stdout
    assert "Overall hit rate: 100.0%" in proc.stdout
    assert "Sufficient contexts:" in proc.stdout
    assert "Tool calls: 1" in proc.stdout
    assert "create-user" in proc.stdout
    assert "hit=100.0%" in proc.stdout


def test_custom_db_flags_work(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    db_path = repo / ".scratch" / "csegraph" / "custom.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)

    indexed = run_cli(
        "index",
        "--repo",
        str(repo),
        "--db",
        str(db_path),
        "--profile",
        "small",
        "--json",
    )
    assert indexed["profile"] == "small"
    assert indexed["db_path"] == str(db_path)

    context = run_cli(
        "context",
        "--repo",
        str(repo),
        "--db",
        str(db_path),
        "--task",
        "Implement create_user",
        "--target",
        "create_user",
        "--json",
    )
    assert context["target"]["id"] == "symbol::service.py::function::create_user"


def test_context_cli_source_controls_and_token_budget(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    run_cli("index", str(repo), "--json")

    compact = run_cli(
        "context",
        "Implement create_user",
        "--target",
        "create_user",
        "--repo",
        str(repo),
        "--include-source",
        "never",
        "--detail-level",
        "standard",
        "--json",
    )
    assert compact["budgets"]["total_estimated_tokens"] >= sum(
        node["estimated_tokens"] for node in compact["symbols"]
    )
    assert compact["import_preludes"]
    assert all("source_text" not in node for node in compact["symbols"])

    budgeted = run_cli(
        "context",
        "Implement create_user",
        "--target",
        "create_user",
        "--repo",
        str(repo),
        "--include-source",
        "always",
        "--max-tokens",
        "50",
        "--detail-level",
        "standard",
        "--json",
    )
    assert budgeted["budgets"]["total_estimated_tokens"] <= 50
    budgeted_nodes = {node["id"]: node for node in budgeted["symbols"]}
    assert "symbol::service.py::function::create_user" in budgeted_nodes
    helper = budgeted_nodes.get("symbol::helpers.py::function::clean_name")
    assert helper is None or helper.get("source_text") is not None


def test_context_config_overrides_thresholds(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    run_cli("index", str(repo), "--json")

    config_file = tmp_path / "csegraph.json"
    config_file.write_text(
        json.dumps({"dep_threshold": 0.65, "confidence_threshold": 0.55}),
        encoding="utf-8",
    )

    context = run_cli(
        "context",
        "Implement create_user with clean_name",
        "--target",
        "create_user",
        "--repo",
        str(repo),
        "--config",
        str(config_file),
        "--json",
    )
    thresholds = context["sufficiency"]["thresholds"]
    assert thresholds["dependency_completeness"] == 0.65
    assert thresholds["model_confidence"] == 0.55
    assert "semantic_overlap_relaxed" in thresholds


def test_context_cli_explain_and_markdown_format(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    run_cli("index", str(repo), "--json")

    explained = run_cli(
        "context",
        "Implement create_user",
        "--target",
        "create_user",
        "--repo",
        str(repo),
        "--explain",
        "--format",
        "json",
    )
    assert all("explanation" in node for node in explained["symbols"])
    helper = next(
        node
        for node in explained["symbols"]
        if node["id"] == "symbol::helpers.py::function::clean_name"
    )
    assert "directly called by the target" in helper["explanation"]

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "csegraph._cli",
            "context",
            "Implement create_user",
            "--target",
            "create_user",
            "--repo",
            str(repo),
            "--format",
            "markdown",
            "--explain",
            "--detail-level",
            "standard",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "# csegraph context" in proc.stdout
    assert "Query: Implement create_user" in proc.stdout
    assert "Requested detail: `standard`" in proc.stdout
    assert "Returned detail: `standard`" in proc.stdout
    assert "## Import Preludes" in proc.stdout
    assert "## Relationships" in proc.stdout
    assert "Reasons: target" in proc.stdout
    assert "Included because" in proc.stdout
    assert "```python" in proc.stdout
    assert "## Next Actions" in proc.stdout
    # Verify next action rendering includes tool and node fields
    assert (
        "`inspect_graph`; tool `csegraph_graph`; node `symbol::service.py::function::create_user`"
        in proc.stdout
    )


def test_context_cli_minimal_markdown_shows_expand_context(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    run_cli("index", str(repo), "--json")

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "csegraph._cli",
            "context",
            "Implement create_user",
            "--target",
            "create_user",
            "--repo",
            str(repo),
            "--format",
            "markdown",
            "--detail-level",
            "minimal",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "# csegraph context" in proc.stdout
    assert "Requested detail: `minimal`" in proc.stdout
    assert "Returned detail: `minimal`" in proc.stdout
    assert "## Next Actions" in proc.stdout
    # Verify expand_context action is shown with detail level
    assert "`expand_context`; detail `standard`" in proc.stdout


def test_context_cli_json_markdown_conflict_fails_clearly(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    run_cli("index", str(repo), "--json")

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "csegraph._cli",
            "context",
            "Implement create_user",
            "--target",
            "create_user",
            "--repo",
            str(repo),
            "--json",
            "--format",
            "markdown",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    err = json.loads(proc.stderr)
    assert err["error_code"] == "invalid_cli_options"
    assert "--json cannot be combined with --format markdown" in err["error"]


def test_context_cli_unsupported_schema_returns_structured_error(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    db_path = repo / ".scratch" / "csegraph" / "future.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta(key, value) VALUES('schema_version', 'csegraph-sqlite-v999');
            """
        )

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "csegraph._cli",
            "context",
            "Implement create_user",
            "--repo",
            str(repo),
            "--db",
            str(db_path),
            "--json",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    err = json.loads(proc.stderr)
    assert err == {
        "error": "Unsupported csegraph index schema. Rerun csegraph index for this repository.",
        "error_code": "unsupported_schema",
        "hint": "Run `csegraph index <repo>` to rebuild this beta index with the current schema.",
    }


def test_cli_help_lists_only_product_commands():
    proc = subprocess.run(
        [sys.executable, "-m", "csegraph._cli", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    public_commands = {
        "doctor",
        "install",
        "index",
        "refresh",
        "postprocess",
        "watch",
        "status",
        "serve",
        "lsp",
        "context",
        "inspect",
        "path",
        "analyze",
        "export",
        "registry",
        "daemon",
    }
    match = re.search(r"\{(?P<commands>[^}]+)\}", proc.stdout)
    assert match
    exposed_commands = set(match.group("commands").split(","))
    assert public_commands == exposed_commands
    for command in (
        "minimal",
        "graph",
        "tree",
        "communities",
        "report",
        "detect-changes",
        "test-gaps",
        "flows",
        "architecture",
        "vulnerabilities",
        "benchmark",
        "review-questions",
        "review-eval",
        "resolvers",
        "embeddings",
        "hooks",
    ):
        assert command not in exposed_commands


def test_fragmented_commands_are_not_public():
    for command in (
        "minimal",
        "graph",
        "tree",
        "report",
        "communities",
        "detect-changes",
        "test-gaps",
        "flows",
        "architecture",
        "vulnerabilities",
        "review-questions",
        "review-eval",
        "benchmark",
        "resolvers",
        "embeddings",
        "hooks",
    ):
        proc = subprocess.run(
            [sys.executable, "-m", "csegraph._cli", command, "--help"],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 2
        assert "invalid choice" in proc.stderr


def test_maintainer_cli_help_lists_only_private_commands():
    proc = subprocess.run(
        [sys.executable, "tools/csegraph_dev.py", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    private_commands = {
        "architecture",
        "benchmark",
        "communities",
        "detect-changes",
        "embeddings",
        "flows",
        "report",
        "resolvers",
        "review-eval",
        "review-questions",
        "test-gaps",
        "vulnerabilities",
    }
    match = re.search(r"\{(?P<commands>[^}]+)\}", proc.stdout)
    assert match
    exposed_commands = set(match.group("commands").split(","))
    assert private_commands == exposed_commands
    for command in (
        "doctor",
        "install",
        "index",
        "refresh",
        "postprocess",
        "watch",
        "status",
        "serve",
        "context",
        "inspect",
        "path",
        "analyze",
        "export",
        "registry",
        "daemon",
        "minimal",
        "graph",
        "tree",
        "hooks",
    ):
        assert command not in exposed_commands


def test_export_html_and_tree_replace_graph_and_tree_commands(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    run_cli("index", str(repo), "--json")

    html = run_cli("export", str(repo), "--format", "html", "--json")
    assert html["command"] == "export"
    assert html["format"] == "html"
    assert html["output_path"].endswith("csegraph-graph.html")

    tree = run_cli("export", str(repo), "--format", "tree", "--json")
    assert tree["command"] == "export"
    assert tree["format"] == "tree"
    assert tree["output_path"].endswith("csegraph-tree.html")


def test_analyze_json_combines_public_diagnostics(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    run_cli("index", str(repo), "--json")

    result = run_cli("analyze", str(repo), "--base-ref", "HEAD", "--json")

    assert result["command"] == "analyze"
    assert result["sections"]
    section_names = {section["name"] for section in result["sections"]}
    assert {"changes", "test_gaps", "architecture", "flows", "security"} <= section_names
    assert isinstance(result["next_actions"], list)


def test_private_maintainer_cli_exposes_benchmark(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    proc = subprocess.run(
        [
            sys.executable,
            "tools/csegraph_dev.py",
            "benchmark",
            str(repo),
            "--target",
            "create_user",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(proc.stdout)
    assert result["command"] == "benchmark"


def _init_git_repo(repo: Path) -> None:
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"], capture_output=True, check=True
    )


def test_detect_changes_json(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    (repo / "core.py").write_text(
        "def target():\n    pass\n\ndef caller():\n    target()\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo), "add", "."], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "initial"], capture_output=True, check=True
    )

    (repo / "core.py").write_text(
        "def target():\n    return 42\n\ndef caller():\n    target()\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo), "add", "."], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "modify"], capture_output=True, check=True
    )

    run_cli("index", str(repo), "--json")
    result = run_dev_cli("detect-changes", str(repo), "--base-ref", "HEAD~1", "--json")

    assert result["command"] == "detect-changes"
    assert result["base_ref"] == "HEAD~1"
    assert "core.py" in result["changed_files"]
    assert result["total_changed_symbols"] >= 1
    all_syms = result["high_risk"] + result["medium_risk"] + result["low_risk"]
    names = {s["name"] for s in all_syms}
    assert "target" in names


def test_detect_changes_human_output(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    (repo / "mod.py").write_text("def leaf():\n    pass\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "initial"], capture_output=True, check=True
    )

    (repo / "mod.py").write_text("def leaf():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "modify"], capture_output=True, check=True
    )

    run_cli("index", str(repo), "--json")

    proc = subprocess.run(
        [
            sys.executable,
            "tools/csegraph_dev.py",
            "detect-changes",
            str(repo),
            "--base-ref",
            "HEAD~1",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Changed symbols:" in proc.stdout
    assert "file(s)" in proc.stdout


def test_detect_changes_no_changes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "initial"], capture_output=True, check=True
    )

    run_cli("index", str(repo), "--json")
    result = run_dev_cli("detect-changes", str(repo), "--base-ref", "HEAD", "--json")

    assert result["total_changed_symbols"] == 0
    assert result["high_risk"] == []


def test_single_package_install_exposes_cli_and_sdk(tmp_path):
    """Root csegraph install should expose CLI and SDK facade from one distribution."""
    repo_root = Path(__file__).resolve().parents[2]
    if not (repo_root / "pyproject.toml").exists():
        import pytest

        pytest.skip("root csegraph package not present in this checkout")

    venv = tmp_path / "v"
    _create_test_venv(venv)
    bin_dir = venv / ("Scripts" if sys.platform.startswith("win") else "bin")
    pip = bin_dir / ("pip.exe" if sys.platform.startswith("win") else "pip")
    csegraph_bin = bin_dir / ("csegraph.exe" if sys.platform.startswith("win") else "csegraph")

    subprocess.run(
        [
            str(pip),
            "install",
            "--quiet",
            "--no-index",
            "--no-build-isolation",
            "--no-deps",
            "-e",
            str(repo_root),
        ],
        check=True,
        env=_offline_pip_env(),
    )

    listing = subprocess.run([str(pip), "list"], check=True, capture_output=True, text=True).stdout
    project_lines = [line for line in listing.splitlines() if line.lower().startswith("csegraph")]
    assert len(project_lines) == 1
    assert project_lines[0].startswith("csegraph ")
    import_check = subprocess.run(
        [
            str(bin_dir / ("python.exe" if sys.platform.startswith("win") else "python")),
            "-c",
            "import csegraph; from csegraph import ContextService; assert ContextService is not None",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=_offline_pip_env(),
    )
    assert import_check.returncode == 0

    sample = tmp_path / "repo"
    _write_repo(sample)
    _env = _offline_pip_env()
    proc = subprocess.run(
        [str(csegraph_bin), "index", str(sample), "--json"],
        check=True,
        capture_output=True,
        text=True,
        env=_env,
    )
    assert json.loads(proc.stdout)["files_indexed"] == 2
    proc = subprocess.run(
        [
            str(csegraph_bin),
            "context",
            "Implement create_user",
            "--target",
            "create_user",
            "--repo",
            str(sample),
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=_env,
    )
    assert json.loads(proc.stdout)["target"]["id"] == "symbol::service.py::function::create_user"
    proc = subprocess.run(
        [
            str(csegraph_bin),
            "inspect",
            "symbol::service.py::function::create_user",
            "--repo",
            str(sample),
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=_env,
    )
    assert json.loads(proc.stdout)["command"] == "inspect"
    proc = subprocess.run(
        [
            str(csegraph_bin),
            "export",
            "--repo",
            str(sample),
            "--format",
            "html",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=_env,
    )
    assert json.loads(proc.stdout)["format"] == "html"
    proc = subprocess.run(
        [str(csegraph_bin), "export", "--repo", str(sample), "--format", "tree", "--json"],
        check=True,
        capture_output=True,
        text=True,
        env=_env,
    )
    assert json.loads(proc.stdout)["format"] == "tree"
    proc = subprocess.run(
        [str(csegraph_bin), "analyze", str(sample), "--base-ref", "HEAD", "--json"],
        check=True,
        capture_output=True,
        text=True,
        env=_env,
    )
    assert json.loads(proc.stdout)["command"] == "analyze"
    proc = subprocess.run(
        [str(csegraph_bin), "status", str(sample), "--json"],
        check=True,
        capture_output=True,
        text=True,
        env=_env,
    )
    assert json.loads(proc.stdout)["command"] == "status"
    proc = subprocess.run(
        [str(csegraph_bin), "postprocess", str(sample), "--json"],
        check=True,
        capture_output=True,
        text=True,
        env=_env,
    )
    assert json.loads(proc.stdout)["command"] == "postprocess"
    proc = subprocess.run(
        [
            str(csegraph_bin),
            "path",
            "symbol::service.py::function::create_user",
            "symbol::helpers.py::function::clean_name",
            "--repo",
            str(sample),
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=_env,
    )
    assert json.loads(proc.stdout)["command"] == "path"
    proc = subprocess.run(
        [str(csegraph_bin), "install", str(sample), "--dry-run", "--json"],
        check=True,
        capture_output=True,
        text=True,
        env=_env,
    )
    assert json.loads(proc.stdout)["command"] == "install"
    proc = subprocess.run(
        [str(csegraph_bin), "refresh", str(sample), "--json"],
        check=True,
        capture_output=True,
        text=True,
        env=_env,
    )
    assert json.loads(proc.stdout)["command"] == "refresh"
