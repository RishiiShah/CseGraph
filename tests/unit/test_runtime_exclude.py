import subprocess

from csegraph._core.languages.registry import registry


def _git(repo, *args):
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def test_cli_exclude_patterns_skip_indexed_file(tmp_path):
    _git(tmp_path, "init")
    (tmp_path / "keep.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "skip.py").write_text("y = 2\n", encoding="utf-8")
    _git(tmp_path, "add", "keep.py", "skip.py")

    pairs = list(registry.iter_files(tmp_path, exclude_patterns=["skip.py"]))
    names = {p.name for _, p in pairs}
    assert "keep.py" in names
    assert "skip.py" not in names
