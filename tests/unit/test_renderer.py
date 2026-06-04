from csegraph._cli.renderer import render_index_summary


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
