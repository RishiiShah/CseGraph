from csegraph._cli.renderer import (
    render_context_markdown,
    render_index_summary,
    render_install_summary,
    render_refresh_summary,
)


def test_render_index_summary_is_lean():
    output = render_index_summary(
        {
            "files_indexed": 2,
            "symbols_indexed": 3,
            "edges_indexed": 1,
            "parse_errors": {"bad.py": "syntax"},
        }
    )
    assert "Index: 2 files, 3 symbols, 1 edges" in output
    assert "ERROR bad.py: syntax" in output
    assert "Profile" not in output
    assert "postprocess" not in output


def test_render_refresh_summary():
    output = render_refresh_summary(
        {
            "changed_files": ["a.py"],
            "deleted_files": ["b.py"],
            "unchanged_files": ["c.py", "d.py"],
        }
    )
    assert output == "Refresh: 1 changed, 1 deleted, 2 unchanged\n"


def test_render_context_markdown_v5():
    output = render_context_markdown(
        {
            "schema_version": "csegraph-context-v5",
            "status": "ready",
            "slices": [
                {
                    "path": "app.py",
                    "lines": [1, 2],
                    "symbol": "run",
                    "role": "target",
                    "code": "def run():\n    pass",
                }
            ],
        }
    )
    assert "`app.py:1-2`" in output
    assert "def run" in output


def test_render_install_summary_shows_next_steps():
    output = render_install_summary(
        {"installed": [{}], "skipped": [], "next_steps": ["Restart the client."]}
    )
    assert "1 configured" in output
    assert "Restart the client." in output
