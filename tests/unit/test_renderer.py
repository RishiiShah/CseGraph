from csegraph._cli.renderer import (
    render_benchmark_summary,
    render_index_summary,
    render_install_summary,
)


def test_render_index_summary_with_parse_errors():
    payload = {
        "files_indexed": 10,
        "symbols_indexed": 25,
        "edges_indexed": 42,
        "profile": "medium",
        "db_path": "/repo/.csegraph/index.db",
        "repo_root": "/repo",
        "parse_errors": {
            "broken.py": "SyntaxError at line 5",
            "bad.py": "IndentationError",
        },
    }

    out = render_index_summary(payload)

    assert "Index: 10 files, 25 symbols, 42 edges" in out
    assert "Parsing: 10 files" in out
    assert "Indexing: 25 symbols, 42 edges (2 parse errors)" in out
    assert "2 parse errors" in out
    assert "postprocess=none" in out
    assert "Cache: 0 hits, 0 misses | Profile: medium | DB: .csegraph/index.db" in out
    assert "Errors:" in out
    assert "broken.py: SyntaxError at line 5" in out
    assert "bad.py: IndentationError" in out
    assert out.index("bad.py: IndentationError") < out.index("broken.py: SyntaxError at line 5")


def test_render_index_summary_shows_absolute_db_path_outside_repo(tmp_path):
    repo_root = tmp_path / "repo"
    outside_db = tmp_path / "outside" / "index.db"
    payload = {
        "files_indexed": 1,
        "symbols_indexed": 2,
        "edges_indexed": 3,
        "profile": "small",
        "db_path": str(outside_db),
        "repo_root": str(repo_root),
        "parse_errors": {},
    }

    out = render_index_summary(payload)

    assert f"DB: {outside_db.resolve()}" in out


def test_render_index_summary_shows_postprocess_totals_inline():
    payload = {
        "files_indexed": 10,
        "symbols_indexed": 25,
        "edges_indexed": 42,
        "profile": "medium",
        "db_path": "/repo/.csegraph/index.db",
        "repo_root": "/repo",
        "cache_hits": 3,
        "cache_misses": 7,
        "parse_errors": {},
        "postprocess_level": "full",
        "postprocess": {
            "fts_entries": 30,
            "communities_detected": 4,
            "resolvers_edges_added": 8,
            "skipped": [],
            "level": "full",
        },
        "graph_totals": {
            "files": 10,
            "nodes": 40,
            "edges": 50,
        },
    }

    out = render_index_summary(payload)

    assert out == (
        "Parsing: 10 files\n"
        "Indexing: 25 symbols, 42 edges\n"
        "Postprocess: FTS 30 rows, 8 inferred edges, 4 communities\n"
        "Full index: 10 files, 40 nodes, 50 edges (postprocess=full)\n"
        "Cache: 3 hits, 7 misses | Profile: medium | DB: .csegraph/index.db\n"
    )


def test_render_benchmark_corpus_summary_shows_sufficiency_and_v3_evidence_counts():
    payload = {
        "command": "benchmark-corpus",
        "repo_root": "/repo",
        "corpus_path": "/repo/benchmarks/context.json",
        "summary": {
            "task_count": 1,
            "passed_task_count": 1,
            "failed_task_count": 0,
            "overall_hit_rate": 1.0,
            "task_pass_rate": 1.0,
            "sufficient_task_count": 1,
            "total_context_tokens": 42,
            "avg_context_tokens": 42.0,
            "total_response_bytes": 512,
            "avg_response_bytes": 512.0,
            "total_tool_call_count": 1,
        },
        "index_stats": {"files": 2, "symbols": 3, "edges": 4, "parse_errors": 0},
        "tasks": [
            {
                "task_id": "auth-evidence",
                "hit_rate": 1.0,
                "context_tokens": 42,
                "response_bytes": 512,
                "tool_call_count": 1,
                "missing_expected_nodes": [],
                "missing_expected_files": [],
                "missing_expected_symbols": [],
                "missing_expected_relationships": [],
                "missing_expected_occurrence_snippets": [],
                "missing_expected_import_preludes": [],
                "violating_forbidden_source_patterns": [],
                "expected_relationship_total": 2,
                "expected_occurrence_snippet_total": 3,
                "expected_import_prelude_total": 1,
                "forbidden_source_pattern_total": 2,
            }
        ],
        "total_elapsed_ms": 12.5,
        "db_path": "/repo/.csegraph/bench.db",
    }

    out = render_benchmark_summary(payload)

    assert "Sufficient contexts: 1 / 1" in out
    assert "relationships=2/2" in out
    assert "occurrences=3/3" in out
    assert "imports=1/1" in out
    assert "forbidden=2/2" in out


def test_render_benchmark_summary_shows_context_quality_signals():
    payload = {
        "command": "benchmark",
        "repo_root": "/repo",
        "db_path": "/repo/.csegraph/bench.db",
        "graph_output_path": "/repo/.csegraph/graph.html",
        "total_elapsed_ms": 10.0,
        "steps": [
            {
                "name": "context",
                "elapsed_ms": 1.25,
                "stats": {
                    "schema_version": "csegraph-context-v3",
                    "returned_detail_level": "standard",
                    "nodes": 3,
                    "total_estimated_tokens": 120,
                    "mcp_response_bytes": 900,
                    "relationship_count": 2,
                    "relationship_occurrence_count": 4,
                    "target_confidence": 0.42,
                    "sufficiency_failure_count": 1,
                    "recovery_action_count": 2,
                    "duplicate_occurrence_count": 1,
                },
            }
        ],
    }

    out = render_benchmark_summary(payload)

    assert "target_conf=0.42" in out
    assert "failures=1" in out
    assert "recovery=2" in out
    assert "dup_occurrences=1" in out


def test_render_context_markdown_reads_relationship_occurrences():
    from csegraph._cli.renderer import render_context_markdown

    payload = {
        "query": "Implement auth flow",
        "target": "symbol::auth.py::function::authenticate_user",
        "sufficiency": {"sufficient": True},
        "request": {
            "task": "Implement auth flow",
            "detail_level": "standard",
            "returned_detail_level": "standard",
        },
        "budgets": {"total_estimated_tokens": 42},
        "token_usage": {
            "estimator": "chars/4 proxy",
            "used_tokens": 12,
            "baseline_tokens": 120,
            "saved_tokens": 108,
            "reduction_ratio": 10.0,
        },
        "symbols": [],
        "relationships": [
            {
                "source": "symbol::auth.py::function::authenticate_user",
                "relation": "calls",
                "target": "symbol::passwords.py::function::verify_password",
                "occurrences": [
                    {
                        "path": "auth.py",
                        "line_range": [6, 6],
                        "kind": "calls",
                        "name": "verify_password",
                        "snippet": "verify_password(password, user['password_hash'])",
                    }
                ],
            }
        ],
        "next_actions": [],
    }

    out = render_context_markdown(payload)

    assert "## Relationships" in out
    assert "Token usage: 12 used, 108 saved vs indexed corpus, 10x reduction" in out
    assert "`auth.py:6-6` calls `verify_password`" in out
    assert "verify_password(password, user['password_hash'])" in out


def test_render_context_markdown_shows_recovery_actions():
    from csegraph._cli.renderer import render_context_markdown

    payload = {
        "query": "Improve architecture",
        "target": "symbol::service.py::function::create_user",
        "sufficiency": {
            "sufficient": False,
            "recovery": [
                {
                    "action": "try_architecture_context",
                    "tool": "csegraph_context",
                    "detail_level": "auto",
                    "reason": "Retrieve context for a concrete subsystem.",
                    "suggested_targets": [
                        {
                            "target": "symbol::service.py::function::create_user",
                            "path": "service.py",
                        }
                    ],
                }
            ],
        },
        "request": {
            "task": "Improve architecture",
            "detail_level": "auto",
            "returned_detail_level": "standard",
        },
        "budgets": {"total_estimated_tokens": 42},
        "symbols": [],
        "relationships": [],
        "next_actions": [],
    }

    out = render_context_markdown(payload)

    assert "## Recovery" in out
    assert "`try_architecture_context`" in out
    assert "target `symbol::service.py::function::create_user` (service.py)" in out


def test_render_install_summary_shows_next_steps():
    payload = {
        "server_name": "csegraph",
        "server_command": "/env/bin/csegraph",
        "server_args": ["serve", "--repo", "/repo", "--platform", "codex"],
        "installed": [
            {
                "platform": "codex",
                "path": "/repo/.codex/config.toml",
                "action": "created",
            }
        ],
        "next_steps": [
            "Open codex's MCP/tools settings and enable or approve the csegraph server.",
            "Confirm the six CseGraph tools are visible.",
        ],
    }

    out = render_install_summary(payload)

    assert "Next steps:" in out
    assert "Open codex's MCP/tools settings" in out
    assert "Confirm the six CseGraph tools" in out
