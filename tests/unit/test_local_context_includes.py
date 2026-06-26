from pathlib import Path

from csegraph._core.ignore import (
    audit_explicit_includes,
    is_safe_explicit_include,
    load_ignore_filter,
)
from csegraph._core.languages.documents import DocumentParser


def test_explicit_include_can_select_gitignored_internal_doc(tmp_path: Path):
    internal = tmp_path / "internal"
    internal.mkdir()
    doc = internal / "architecture.md"
    doc.write_text("# Payment Architecture\n\nLedger writes are idempotent.\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("internal/\n", encoding="utf-8")
    (tmp_path / ".csegraphinclude").write_text("internal/*.md\n", encoding="utf-8")

    ignore = load_ignore_filter(tmp_path)

    assert ignore.is_explicitly_included("internal/architecture.md")
    assert not ignore.is_ignored("internal/architecture.md")


def test_explicit_include_blocks_secret_material(tmp_path: Path):
    private = tmp_path / "internal"
    private.mkdir()
    (private / "architecture.md").write_text("# Safe\n", encoding="utf-8")
    (private / "credentials.json").write_text('{"token": "secret"}\n', encoding="utf-8")
    (private / "service.key").write_text("private-key\n", encoding="utf-8")
    (tmp_path / ".csegraphinclude").write_text("internal/*\n", encoding="utf-8")

    ignore = load_ignore_filter(tmp_path)

    assert ignore.is_explicitly_included("internal/architecture.md")
    assert not ignore.is_explicitly_included("internal/credentials.json")
    assert not ignore.is_explicitly_included("internal/service.key")
    assert not is_safe_explicit_include(".env.local")


def test_explicit_include_audit_reports_included_blocked_and_unmatched(tmp_path: Path):
    internal = tmp_path / "internal"
    internal.mkdir()
    (internal / "architecture.md").write_text("# Safe\n", encoding="utf-8")
    (internal / "credentials.json").write_text('{"token": "secret"}\n', encoding="utf-8")
    (tmp_path / ".csegraphinclude").write_text(
        "internal/*\nmissing/*.md\n",
        encoding="utf-8",
    )

    audit = audit_explicit_includes(tmp_path)

    assert audit["configured"] is True
    assert audit["included"] == [{"path": "internal/architecture.md", "pattern": "internal/*"}]
    assert audit["blocked"] == [
        {
            "path": "internal/credentials.json",
            "pattern": "internal/*",
            "reason": "sensitive_path",
        }
    ]
    assert audit["unmatched_patterns"] == ["missing/*.md"]


def test_document_parser_produces_retrievable_source_symbol(tmp_path: Path):
    doc = tmp_path / "design.md"
    doc.write_text("# Queue Design\n\nWorkers use lease-based retries.\n", encoding="utf-8")

    parsed = DocumentParser().parse(doc, tmp_path)

    assert parsed.language == "document"
    assert parsed.symbols[0].kind == "document"
    assert parsed.symbols[0].name == "Queue Design"
    assert "lease-based retries" in parsed.symbols[0].source
