from csegraph_cli.renderer import render_index_summary


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

    assert "Parsing: 10 files" in out
    assert "Indexing: 25 symbols, 42 edges" in out
    assert "2 parse errors" in out
    assert "  Files:      10" in out
    assert "  Symbols:    25" in out
    assert "  Edges:      42" in out
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

    assert f"DB:         {outside_db.resolve()}" in out
