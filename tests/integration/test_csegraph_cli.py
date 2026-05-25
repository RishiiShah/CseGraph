import json
import os
import re
import site
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import csegraph_cli.main as cli_main
from csegraph_core.retrieval.constants import VALID_REASONS

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
    env["PYTHONPATH"] = os.pathsep.join(site.getsitepackages())
    return env


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
    assert context["query"] == "Implement create_user with clean_name"
    assert context["target"] == "symbol::service.py::function::create_user"
    assert context["detail_level"] == "auto"
    assert context["returned_detail_level"] == "minimal"
    assert context["sufficiency"]["sufficient"] is True
    assert "target_node_id" not in context
    assert "context_nodes" not in context
    assert "estimated_tokens" not in context
    assert "metrics" not in context
    assert "thresholds" not in context
    assert "is_sufficient" not in context
    assert any(
        node["id"] == "symbol::helpers.py::function::clean_name"
        for node in context["nodes"]
    )
    assert context["total_estimated_tokens"] >= 1
    canonical_by_id = {node["id"]: node for node in context["nodes"]}
    target_node = canonical_by_id["symbol::service.py::function::create_user"]
    helper_node = canonical_by_id["symbol::helpers.py::function::clean_name"]
    assert target_node["path"] == "service.py"
    assert target_node["line_range"] == [3, 4]
    assert "target" in target_node["reason"]
    if "symbol::helpers.py::function::clean_name" in canonical_by_id:
        assert "direct_call" in helper_node["reason"]
    assert all(
        reason in VALID_REASONS
        for node in context["nodes"]
        for reason in node["reason"]
    )
    assert all("expanded-from-" not in reason for node in context["nodes"] for reason in node["reason"])
    assert all("explanation" not in node for node in context["nodes"])
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
    standard_by_id = {node["id"]: node for node in standard_context["nodes"]}
    assert standard_context["returned_detail_level"] == "standard"
    assert "def create_user(name: str) -> dict:" in standard_by_id[
        "symbol::service.py::function::create_user"
    ]["source_text"]
    assert "def clean_name(value: str) -> str:" in standard_by_id[
        "symbol::helpers.py::function::clean_name"
    ]["source_text"]

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
        [sys.executable, "-m", "csegraph_cli", "index", str(repo)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Parsing:" in proc.stdout
    assert "2 files" in proc.stdout
    assert "Indexing:" in proc.stdout
    assert "symbols" in proc.stdout
    assert "edges" in proc.stdout
    assert "  Files:" in proc.stdout
    assert "  Symbols:" in proc.stdout
    assert "  Edges:" in proc.stdout
    assert "  Cache:" in proc.stdout
    assert "  Profile:" in proc.stdout
    assert "  DB:" in proc.stdout


def test_refresh_default_output_is_human_summary(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    run_cli("index", str(repo), "--json")

    proc = subprocess.run(
        [sys.executable, "-m", "csegraph_cli", "refresh", str(repo)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Scanning:" in proc.stdout
    assert "  Changed:" in proc.stdout
    assert "  Unchanged:" in proc.stdout
    assert "  Cache:" in proc.stdout
    assert "  Profile:" in proc.stdout
    assert "  DB:" in proc.stdout


def test_index_json_flag_returns_parseable_json(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)

    result = run_cli("index", str(repo), "--json")

    assert result["command"] == "index"
    assert result["files_indexed"] == 2
    assert result["cache_hits"] == 0
    assert result["cache_misses"] == 2
    assert isinstance(result["changed_files"], list)


def test_refresh_json_flag_returns_parseable_json(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    run_cli("index", str(repo), "--json")

    result = run_cli("refresh", str(repo), "--json")

    assert result["command"] == "refresh"
    assert result["cache_hits"] == 2
    assert result["cache_misses"] == 0
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

    assert result["detail_level"] == "full"
    assert result["returned_detail_level"] == "full"
    assert any("explanation" in node for node in result["nodes"])
    assert all("source_text" in node for node in result["nodes"])


def test_help_lists_canonical_index_and_refresh_without_aliases():
    proc = subprocess.run(
        [sys.executable, "-m", "csegraph_cli", "--help"],
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
            [sys.executable, "-m", "csegraph_cli", command, "--help"],
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
    result = run_cli("install", str(tmp_path), "--dry-run", "--json")

    assert result["command"] == "install"
    assert result["dry_run"] is True
    assert result["server_command"] == "csegraph"
    assert result["server_args"] == ["serve"]
    assert {target["platform"] for target in result["installed"]} == {"claude-code"}


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


def test_install_codex_dry_run_json_uses_user_config(tmp_path):
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
    assert [step["name"] for step in steps] == ["index", "refresh", "context", "graph", "report", "token_reduction"]
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
    assert all(
        elapsed_ms >= 0
        for elapsed_ms in by_name["index"]["stats"]["phases"].values()
    )
    assert by_name["refresh"]["stats"]["changed_files"] == 0
    assert by_name["refresh"]["stats"]["deleted_files"] == 0
    assert by_name["context"]["stats"]["nodes"] >= 1
    assert by_name["context"]["stats"]["target"] == "symbol::service.py::function::create_user"
    assert by_name["context"]["stats"]["schema_version"] == "csegraph-context-v2"
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


def test_custom_db_flags_work(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "custom.db"
    _write_repo(repo)

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
        "--db",
        str(db_path),
        "--task",
        "Implement create_user",
        "--target",
        "create_user",
        "--json",
    )
    assert context["target"] == "symbol::service.py::function::create_user"


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
    assert compact["total_estimated_tokens"] == sum(
        node["estimated_tokens"] for node in compact["nodes"]
    )
    assert all("source_text" not in node for node in compact["nodes"])

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
    assert budgeted["total_estimated_tokens"] <= 50
    budgeted_nodes = {node["id"]: node for node in budgeted["nodes"]}
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
    assert all("explanation" in node for node in explained["nodes"])
    helper = next(
        node
        for node in explained["nodes"]
        if node["id"] == "symbol::helpers.py::function::clean_name"
    )
    assert "directly called by the target" in helper["explanation"]

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "csegraph_cli",
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
    assert "Reasons: target" in proc.stdout
    assert "Included because" in proc.stdout
    assert "```python" in proc.stdout
    assert "## Next Actions" in proc.stdout
    # Verify next action rendering includes tool and node fields
    assert "`inspect_graph`; tool `csegraph_graph`; node `symbol::service.py::function::create_user`" in proc.stdout


def test_context_cli_minimal_markdown_shows_expand_context(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    run_cli("index", str(repo), "--json")

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "csegraph_cli",
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
            "csegraph_cli",
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
    db_path = tmp_path / "future.db"
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
            "csegraph_cli",
            "context",
            "Implement create_user",
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
        "error": "Unsupported csegraph index schema",
        "error_code": "unsupported_schema",
        "hint": "Rebuild the index with the current csegraph-core version.",
    }


def test_cli_help_lists_only_product_commands():
    proc = subprocess.run(
        [sys.executable, "-m", "csegraph_cli", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    public_commands = {
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
            [sys.executable, "-m", "csegraph_cli", command, "--help"],
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
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@test.com"], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], capture_output=True, check=True)


def test_detect_changes_json(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    (repo / "core.py").write_text(
        "def target():\n    pass\n\ndef caller():\n    target()\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo), "add", "."], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "initial"], capture_output=True, check=True)

    (repo / "core.py").write_text(
        "def target():\n    return 42\n\ndef caller():\n    target()\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo), "add", "."], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "modify"], capture_output=True, check=True)

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
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "initial"], capture_output=True, check=True)

    (repo / "mod.py").write_text("def leaf():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "modify"], capture_output=True, check=True)

    run_cli("index", str(repo), "--json")

    proc = subprocess.run(
        [sys.executable, "tools/csegraph_dev.py", "detect-changes", str(repo), "--base-ref", "HEAD~1"],
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
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "initial"], capture_output=True, check=True)

    run_cli("index", str(repo), "--json")
    result = run_dev_cli("detect-changes", str(repo), "--base-ref", "HEAD", "--json")

    assert result["total_changed_symbols"] == 0
    assert result["high_risk"] == []


def test_install_matrix_cli_works_without_sdk(tmp_path):
    """CLI should run with root csegraph-core + csegraph-cli, no SDK package."""
    repo_root = Path(__file__).resolve().parents[2]
    if not (repo_root / "pyproject.toml").exists():
        import pytest
        pytest.skip("root csegraph-core package not present in this checkout")

    venv = tmp_path / "v"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    bin_dir = venv / ("Scripts" if sys.platform.startswith("win") else "bin")
    pip = bin_dir / ("pip.exe" if sys.platform.startswith("win") else "pip")
    csegraph_bin = bin_dir / ("csegraph.exe" if sys.platform.startswith("win") else "csegraph")

    subprocess.run(
        [str(pip), "install", "--quiet", "--no-index", "--no-build-isolation", "--no-deps",
         "-e", str(repo_root),
         "-e", str(repo_root / "packages" / "csegraph-cli")],
        check=True,
        env=_offline_pip_env(),
    )

    # SDK must NOT be installed in this venv.
    listing = subprocess.run([str(pip), "list"], check=True, capture_output=True, text=True).stdout
    assert "csegraph-core" in listing
    assert "csegraph-cli" in listing
    sdk_lines = [
        line for line in listing.splitlines()
        if line.startswith("csegraph ") or line.split()[0:1] == ["csegraph"]
    ]
    assert sdk_lines == [], f"SDK should not be installed: {sdk_lines}"
    # Core packaged CLI commands must work without installing the SDK package.
    sample = tmp_path / "repo"
    _write_repo(sample)
    _env = _offline_pip_env()
    proc = subprocess.run(
        [str(csegraph_bin), "index", str(sample), "--json"],
        check=True, capture_output=True, text=True, env=_env,
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
    assert json.loads(proc.stdout)["target"] == "symbol::service.py::function::create_user"
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
