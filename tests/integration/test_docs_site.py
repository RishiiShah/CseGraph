from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_mkdocs_site_scaffold_references_existing_pages():
    config = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")

    assert "site_name: CseGraph" in config
    assert "name: material" in config

    for doc_path in (
        "index.md",
        "csegraph.md",
        "architecture.md",
        "benchmarks.md",
    ):
        assert (ROOT / "docs" / doc_path).exists()
        assert doc_path in config
