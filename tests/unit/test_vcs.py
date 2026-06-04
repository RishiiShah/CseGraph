from pathlib import Path

from csegraph._core.vcs import find_svn_root, svn_versioned_paths


def test_find_svn_root_returns_topmost(tmp_path):
    wc = tmp_path / "wc"
    sub = wc / "pkg" / "src"
    sub.mkdir(parents=True)
    (wc / ".svn").mkdir()
    (sub / ".svn").mkdir()

    assert find_svn_root(sub) == wc.resolve()


def test_svn_versioned_paths_filters_to_existing_files(tmp_path, monkeypatch):
    wc = tmp_path / "wc"
    wc.mkdir()
    (wc / ".svn").mkdir()
    (wc / "keep.py").write_text("x = 1\n", encoding="utf-8")

    import subprocess

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, "keep.py\nmissing.py\n", "")

    monkeypatch.setattr("csegraph._core.vcs.subprocess.run", fake_run)

    paths = svn_versioned_paths(wc, wc)
    assert paths == {"keep.py"}
