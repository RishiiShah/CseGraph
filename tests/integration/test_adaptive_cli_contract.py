from csegraph._cli.renderer import render_context_markdown


def test_adaptive_markdown_labels_token_measurement():
    rendered = render_context_markdown(
        {
            "schema_version": "csegraph-context-v4",
            "status": "ready",
            "intent": "understand",
            "target": {"id": "symbol::app.py::function::greet"},
            "usage": {
                "tokens": 123,
                "budget": 800,
                "encoding": "o200k_base",
                "measurement": "estimated",
            },
            "freshness": {"state": "current", "revision": 4},
            "slices": [],
        }
    )

    assert "Tokens: 123 / 800 (`o200k_base`, estimated)" in rendered
