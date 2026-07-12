from csegraph._cli.renderer import render_context_markdown


def test_v5_markdown_renders_status_slices_and_continuation():
    rendered = render_context_markdown(
        {
            "schema_version": "csegraph-context-v5",
            "status": "ambiguous",
            "slices": [],
            "candidates": [
                {"id": "symbol::app.py::function::run", "path": "app.py", "lines": [1, 2]}
            ],
            "next": {
                "tool": "csegraph_context",
                "arguments": {"target": "symbol::app.py::function::run"},
                "reason": "Choose a target.",
            },
        }
    )

    assert "ambiguous" in rendered
    assert "app.py" in rendered
    assert "csegraph_context" in rendered
    assert "Arguments" in rendered
