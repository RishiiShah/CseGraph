import json
import subprocess
import sys
from pathlib import Path


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


def _run_cli(*args: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "csegraph_cli", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(proc.stdout)


def test_csegraphignore_excludes_files_from_index(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)

    (repo / ".csegraphignore").write_text(
        "generated.py\nscripts/\n",
        encoding="utf-8",
    )

    result = _run_cli("index", str(repo), "--json")
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

    result = _run_cli("index", str(repo), "--json")
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

    result = _run_cli("index", str(repo), "--json")
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

    result = _run_cli("index", str(repo), "--json")
    indexed_files = result["changed_files"]
    assert "generated.py" not in indexed_files
    assert "sub/generated.py" in indexed_files


def test_refresh_removes_newly_ignored_files(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)

    result = _run_cli("index", str(repo), "--json")
    assert result["files_indexed"] == 4

    (repo / ".csegraphignore").write_text(
        "generated.py\nscripts/\n",
        encoding="utf-8",
    )

    refreshed = _run_cli("refresh", str(repo), "--json")
    assert "generated.py" in refreshed["deleted_files"]
    assert any("scripts/" in f for f in refreshed["deleted_files"])


def test_no_csegraphignore_indexes_everything(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)

    result = _run_cli("index", str(repo), "--json")
    assert result["files_indexed"] == 4
