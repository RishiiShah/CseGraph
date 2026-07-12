from __future__ import annotations

import json
from pathlib import Path

from csegraph._cli.renderer import render_context_markdown
from csegraph._core.core.models import ContextRequest
from csegraph._core.core.serializer import to_dict
from csegraph._core.index.services import IndexService
from csegraph._core.retrieval.context import ContextService


def test_context_json_and_markdown_render_the_same_slices(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "service.py").write_text(
        "def create_user(name: str) -> dict:\n    return {'name': name}\n",
        encoding="utf-8",
    )
    db = repo / ".csegraph" / "index.db"
    IndexService(db).index(repo)
    payload = to_dict(
        ContextService(db).retrieve(
            ContextRequest(
                repo=str(repo),
                task="Change create_user",
                target="create_user",
            )
        )
    )

    markdown = render_context_markdown(payload)

    assert json.loads(json.dumps(payload)) == payload
    for slice_ in payload["slices"]:
        assert slice_["path"] in markdown
        assert slice_["code"].strip() in markdown


def test_compact_contract_omits_empty_optional_sections(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    db = repo / ".csegraph" / "index.db"
    IndexService(db).index(repo)

    payload = to_dict(
        ContextService(db).retrieve(
            ContextRequest(repo=str(repo), task="Explain run", target="run")
        )
    )

    assert "candidates" not in payload
    assert "missing" not in payload
    assert "warnings" not in payload
    assert "diagnostics" not in payload
