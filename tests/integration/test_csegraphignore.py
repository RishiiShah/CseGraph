import subprocess
from pathlib import Path

from tests.conftest import run_cli


def _write_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "helpers.py").write_text(
        "def clean_name(value: str) -> str:\n    return value.strip().lower()\n",
        encoding="utf-8",
    )
    (root / "service.py").write_text(
        "from helpers import clean_name\n\n"
        "def create_user(name: str) -> dict:\n    return {'name': clean_name(name)}\n",
        encoding="utf-8",
    )
    (root / "generated.py").write_text(
        "def generated_func():\n    pass\n",
        encoding="utf-8",
    )
    scripts = root / "scripts"
    scripts.mkdir()
    (scripts / "deploy.py").write_text(
        "def deploy():\n    pass\n",
        encoding="utf-8",
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def test_csegraphignore_excludes_files_from_index(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)

    (repo / ".csegraphignore").write_text(
        "generated.py\nscripts/\n",
        encoding="utf-8",
    )

    result = run_cli("index", str(repo), "--json")
    assert result["files_indexed"] == 2
    indexed_files = result["changed_files"]
    assert "generated.py" not in indexed_files
    assert all("scripts/" not in f for f in indexed_files)
    assert "helpers.py" in indexed_files
    assert "service.py" in indexed_files


def test_csegraphignore_glob_pattern(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)

    (repo / ".csegraphignore").write_text(
        "*.generated.py\n",
        encoding="utf-8",
    )
    (repo / "models.generated.py").write_text(
        "class Model:\n    pass\n",
        encoding="utf-8",
    )

    result = run_cli("index", str(repo), "--json")
    indexed_files = result["changed_files"]
    assert "models.generated.py" not in indexed_files
    assert "generated.py" in indexed_files


def test_csegraphignore_negation(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)

    (repo / "a.log.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "important.log.py").write_text("y = 2\n", encoding="utf-8")

    (repo / ".csegraphignore").write_text(
        "*.log.py\n!important.log.py\n",
        encoding="utf-8",
    )

    result = run_cli("index", str(repo), "--json")
    indexed_files = result["changed_files"]
    assert "a.log.py" not in indexed_files
    assert "important.log.py" in indexed_files


def test_csegraphignore_rooted_pattern(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)

    sub = repo / "sub"
    sub.mkdir()
    (sub / "generated.py").write_text("z = 3\n", encoding="utf-8")

    (repo / ".csegraphignore").write_text(
        "/generated.py\n",
        encoding="utf-8",
    )

    result = run_cli("index", str(repo), "--json")
    indexed_files = result["changed_files"]
    assert "generated.py" not in indexed_files
    assert "sub/generated.py" in indexed_files


def test_refresh_removes_newly_ignored_files(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)

    result = run_cli("index", str(repo), "--json")
    assert result["files_indexed"] == 4

    (repo / ".csegraphignore").write_text(
        "generated.py\nscripts/\n",
        encoding="utf-8",
    )

    refreshed = run_cli("refresh", str(repo), "--json")
    assert "generated.py" in refreshed["deleted_files"]
    assert any("scripts/" in f for f in refreshed["deleted_files"])


def test_no_csegraphignore_indexes_everything(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)

    result = run_cli("index", str(repo), "--json")
    assert result["files_indexed"] == 4


def test_gitignore_excludes_untracked_files_from_index(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    _git(repo, "init")
    (repo / ".gitignore").write_text("generated.py\nscripts/\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "helpers.py", "service.py")

    result = run_cli("index", str(repo), "--json")

    indexed_files = result["changed_files"]
    assert "helpers.py" in indexed_files
    assert "service.py" in indexed_files
    assert "generated.py" not in indexed_files
    assert all("scripts/" not in path for path in indexed_files)


def test_tracked_gitignored_file_stays_indexed(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    _git(repo, "init")
    (repo / ".gitignore").write_text("generated.py\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "helpers.py", "service.py")
    _git(repo, "add", "-f", "generated.py")

    result = run_cli("index", str(repo), "--json")

    indexed_files = result["changed_files"]
    assert "generated.py" in indexed_files


def test_untracked_files_not_indexed_in_git_repo(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    _git(repo, "init")
    _git(repo, "add", "helpers.py", "service.py")

    ref = repo / "ref"
    ref.mkdir()
    (ref / "notes.py").write_text("SECRET = 1\n", encoding="utf-8")

    result = run_cli("index", str(repo), "--json")
    indexed_files = result["changed_files"]
    assert "helpers.py" in indexed_files
    assert "service.py" in indexed_files
    assert all("ref/" not in path for path in indexed_files)
    assert "generated.py" not in indexed_files


def test_local_include_indexes_gitignored_internal_document(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    (repo / "app.py").write_text("def charge() -> str:\n    return 'ok'\n", encoding="utf-8")
    internal = repo / "internal"
    internal.mkdir()
    (internal / "payments.md").write_text(
        "# Payment Invariants\n\nLedger writes must be idempotent.\n",
        encoding="utf-8",
    )
    (internal / "service.key").write_text("do-not-index\n", encoding="utf-8")
    (repo / ".gitignore").write_text("internal/\n.csegraphinclude\n", encoding="utf-8")
    (repo / ".csegraphinclude").write_text("internal/*\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "app.py")

    result = run_cli("index", str(repo), "--json")

    assert "internal/payments.md" in result["changed_files"]
    assert "internal/service.key" not in result["changed_files"]

    context = run_cli(
        "context",
        "Where are ledger writes required to be idempotent?",
        "--repo",
        str(repo),
        "--target",
        "Payment Invariants",
        "--detail-level",
        "standard",
        "--json",
    )
    document = next(
        symbol for symbol in context["symbols"] if symbol["name"] == "Payment Invariants"
    )
    assert "Ledger writes must be idempotent" in document["source_text"]


def test_gitignored_internal_document_requires_explicit_local_consent(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    (repo / "app.py").write_text("def charge() -> str:\n    return 'ok'\n", encoding="utf-8")
    internal = repo / "internal"
    internal.mkdir()
    (internal / "private-design.md").write_text(
        "# Private Design\n\nThis must not enter agent context.\n",
        encoding="utf-8",
    )
    (repo / ".gitignore").write_text("internal/\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "app.py")

    result = run_cli("index", str(repo), "--json")

    assert "internal/private-design.md" not in result["changed_files"]
    context = run_cli(
        "context",
        "private design agent context",
        "--repo",
        str(repo),
        "--detail-level",
        "standard",
        "--json",
    )
    assert all(symbol["path"] != "internal/private-design.md" for symbol in context["symbols"])


def test_staged_file_indexed_before_commit(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    _git(repo, "init")
    _git(repo, "add", "helpers.py", "service.py")
    (repo / "new_module.py").write_text("def new():\n    pass\n", encoding="utf-8")
    _git(repo, "add", "new_module.py")

    result = run_cli("index", str(repo), "--json")
    assert "new_module.py" in result["changed_files"]


def test_csegraphignore_excludes_tracked_git_file(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    _git(repo, "init")
    (repo / ".csegraphignore").write_text("generated.py\n", encoding="utf-8")
    _git(repo, "add", ".csegraphignore", "helpers.py", "service.py", "generated.py")

    result = run_cli("index", str(repo), "--json")

    indexed_files = result["changed_files"]
    assert "generated.py" not in indexed_files
