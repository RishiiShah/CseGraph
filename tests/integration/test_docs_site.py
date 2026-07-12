from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_tracked_docs_stay_small_and_intentional():
    assert not (ROOT / "mkdocs.yml").exists()

    for doc_path in ("architecture.md", "benchmarks.md"):
        assert (ROOT / "docs" / doc_path).exists()

    for doc_path in ("index.md", "csegraph.md", "roadmap.md"):
        assert not (ROOT / "docs" / doc_path).exists()
