import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from tests.conftest import run_cli


ROOT = Path(__file__).resolve().parents[2]


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(ROOT) if not existing else str(ROOT) + os.pathsep + existing
    return env


def _write_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "helpers.py").write_text(
        "def clean_name(value: str) -> str:\n    return value.strip().lower()\n",
        encoding="utf-8",
    )
    (root / "service.py").write_text(
        "from helpers import clean_name\n\ndef create_user(name: str) -> dict:\n    return {'name': clean_name(name)}\n",
        encoding="utf-8",
    )


def _write_nested_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    package = root / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "worker.py").write_text(
        "def run() -> str:\n    return 'ok'\n",
        encoding="utf-8",
    )


def _html_graph_data(content: str) -> dict:
    match = re.search(r"var DATA = (\{.*?\});", content)
    assert match is not None
    return json.loads(match.group(1))


def test_inspect_json_matches_neighborhood_contract(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    run_cli("index", str(repo), "--json")

    result = run_cli(
        "inspect",
        "symbol::service.py::function::create_user",
        "--repo",
        str(repo),
        "--depth",
        "1",
        "--detail-level",
        "standard",
        "--json",
    )
    assert result["command"] == "inspect"
    assert result["target"] == "symbol::service.py::function::create_user"
    assert result["depth"] == 1
    assert isinstance(result["nodes"], list)
    assert isinstance(result["edges"], list)
    assert any(edge["relation"] == "calls" for edge in result["edges"])


def test_inspect_default_output_is_pretty_json(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    run_cli("index", str(repo), "--json")

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "csegraph._cli",
            "inspect",
            "create_user",
            "--repo",
            str(repo),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    parsed = json.loads(proc.stdout)
    assert parsed["command"] == "inspect"
    assert "\n" in proc.stdout


def test_graph_rejects_node_argument(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    run_cli("index", str(repo), "--json")

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "csegraph._cli",
            "graph",
            "symbol::service.py::function::create_user",
            "--repo",
            str(repo),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert "invalid choice" in proc.stderr


def test_graph_rejects_node_flag_and_depth(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    run_cli("index", str(repo), "--json")

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "csegraph._cli",
            "export",
            "--repo",
            str(repo),
            "--format",
            "html",
            "--node",
            "create_user",
            "--depth",
            "1",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert "unrecognized arguments" in proc.stderr
    assert "--node" in proc.stderr
    assert "--depth" in proc.stderr


def test_graph_visual_export_creates_html(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    run_cli("index", str(repo), "--json")

    output_html = repo / ".scratch" / "csegraph" / "out.html"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "csegraph._cli",
            "export",
            "--repo",
            str(repo),
            "--format",
            "html",
            "--output",
            str(output_html),
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(proc.stdout)
    assert result["command"] == "export"
    assert result["format"] == "html"
    assert result["output_path"] == str(output_html)
    assert result["total_nodes"] >= 2
    assert result["total_edges"] >= 1

    content = output_html.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in content
    assert "csegraph" in content
    assert "create_user" in content
    assert "clean_name" in content
    assert "background: #f8fafc" in content
    assert "color: #334155" in content
    assert "#0d1117" not in content
    assert "var INITIAL_SPREAD_X = Math.max(1400, W * 2.4);" in content
    assert "var INITIAL_SPREAD_Y = Math.max(900, H * 2.0);" in content
    assert "var camX = 0, camY = 0, camZ = INITIAL_ZOOM;" in content
    assert "SPRING_LEN = 180" in content
    assert "REPULSION = 2600" in content
    assert "fx += (0 - sim[i].x) * CENTER;" in content
    assert "fy += (0 - sim[i].y) * CENTER;" in content
    assert "<script" in content
    assert "http://" not in content.split("<script")[1]
    assert "https://" not in content.split("<script")[1]


def test_graph_visual_export_connects_folders_and_empty_files(tmp_path):
    repo = tmp_path / "repo"
    _write_nested_repo(repo)
    run_cli("index", str(repo), "--json")

    output_html = repo / ".scratch" / "csegraph" / "out.html"
    result = run_cli(
        "export",
        "--repo",
        str(repo),
        "--format",
        "html",
        "--output",
        str(output_html),
        "--json",
    )

    data = _html_graph_data(output_html.read_text(encoding="utf-8"))
    edges = {(edge["source"], edge["relation"], edge["target"]) for edge in data["edges"]}
    assert result["total_edges"] == len(data["edges"])
    assert len(edges) == len(data["edges"])
    assert ("repo::repo", "contains", "folder::pkg") in edges
    assert ("folder::pkg", "contains", "file::pkg/__init__.py") in edges
    assert ("folder::pkg", "contains", "file::pkg/worker.py") in edges


def test_graph_visual_export_has_click_to_expand_nodes(tmp_path):
    repo = tmp_path / "repo"
    _write_nested_repo(repo)
    run_cli("index", str(repo), "--json")

    output_html = repo / ".scratch" / "csegraph" / "out.html"
    run_cli(
        "export",
        "--repo",
        str(repo),
        "--format",
        "html",
        "--output",
        str(output_html),
        "--json",
    )

    content = output_html.read_text(encoding="utf-8")
    data = _html_graph_data(content)
    pkg = next(node for node in data["nodes"] if node["id"] == "folder::pkg")
    assert pkg["parent_id"] == "repo::repo"
    assert pkg["child_count"] == 2
    assert "function toggleExpanded(i)" in content
    assert "toggleExpanded(hit);" in content
    assert "function visibleNodeSet()" in content


def test_graph_visual_export_uses_cached_search_and_linear_edge_ticks(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    run_cli("index", str(repo), "--json")

    output_html = repo / ".scratch" / "csegraph" / "out.html"
    run_cli(
        "export",
        "--repo",
        str(repo),
        "--format",
        "html",
        "--output",
        str(output_html),
        "--json",
    )

    content = output_html.read_text(encoding="utf-8")
    data = _html_graph_data(content)
    create_user = next(node for node in data["nodes"] if node["name"] == "create_user")
    assert "create_user" in create_user["search_text"]
    assert "service.py" in create_user["search_text"]
    assert "symbol::service.py::function::create_user" in create_user["search_text"]
    assert "function rebuildVisibleState()" in content
    assert "var visibleNodeIds = [];" in content
    assert "var activeEdges = [];" in content
    assert "for (var ei = 0; ei < activeEdges.length; ei++)" in content
    assert "function scheduleRender()" in content
    assert "n.name.toLowerCase()" not in content


def test_graph_visual_export_has_default_pan_zoom_drag_interactions(tmp_path):
    repo = tmp_path / "repo"
    _write_nested_repo(repo)
    run_cli("index", str(repo), "--json")

    output_html = repo / ".scratch" / "csegraph" / "out.html"
    run_cli(
        "export",
        "--repo",
        str(repo),
        "--format",
        "html",
        "--output",
        str(output_html),
        "--json",
    )

    content = output_html.read_text(encoding="utf-8")
    assert "function toWorld(x, y)" in content
    assert "canvas.addEventListener(\"wheel\"" in content
    assert "canvas.addEventListener(\"mousemove\"" in content
    assert "canvas.addEventListener(\"mouseup\"" in content
    assert "dragNodeIndex" in content
    assert "panStart" in content
    assert "function showDetail(i)" in content


def test_graph_visual_export_default_output_path(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    run_cli("index", str(repo), "--json")

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "csegraph._cli",
            "export",
            "--repo",
            str(repo),
            "--format",
            "html",
            "--json",
        ],
        check=True,
        capture_output=True,
        cwd=tmp_path,
        env=_subprocess_env(),
        text=True,
    )
    result = json.loads(proc.stdout)
    assert result["command"] == "export"
    assert result["format"] == "html"
    expected_path = repo / ".csegraph" / "csegraph-graph.html"
    assert result["output_path"] == str(expected_path)
    assert expected_path.exists()
    assert not (tmp_path / "csegraph-graph.html").exists()
    assert proc.stderr == ""


def test_graph_visual_export_default_output_is_concise_message(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    run_cli("index", str(repo), "--json")

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "csegraph._cli",
            "export",
            "--repo",
            str(repo),
            "--format",
            "html",
        ],
        check=True,
        capture_output=True,
        cwd=tmp_path,
        env=_subprocess_env(),
        text=True,
    )

    expected_path = repo / ".csegraph" / "csegraph-graph.html"
    assert proc.stdout.startswith("Exported html:")
    assert f"  Output: {expected_path}\n" in proc.stdout
    assert "  Files written: 1\n" in proc.stdout
    assert proc.stderr == ""
    assert expected_path.exists()


def test_graph_visual_export_default_output_follows_custom_db_path(tmp_path):
    repo = tmp_path / "repo"
    db_path = repo / ".scratch" / "csegraph" / "custom-index.db"
    _write_repo(repo)
    run_cli("index", "--repo", str(repo), "--db", str(db_path), "--json")

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "csegraph._cli",
            "export",
            "--repo",
            str(repo),
            "--db",
            str(db_path),
            "--format",
            "html",
            "--json",
        ],
        check=True,
        capture_output=True,
        cwd=tmp_path,
        env=_subprocess_env(),
        text=True,
    )
    result = json.loads(proc.stdout)
    expected_path = db_path.with_name("csegraph-graph.html")
    assert result["output_path"] == str(expected_path)
    assert expected_path.exists()


def test_graph_visual_export_has_clean_stderr(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    run_cli("index", str(repo), "--json")

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "csegraph._cli",
            "export",
            "--repo",
            str(repo),
            "--format",
            "html",
            "--output",
            str(repo / ".scratch" / "csegraph" / "g.html"),
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert proc.stderr == ""


def test_graph_visual_export_allows_repo_local_scratch_output(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    run_cli("index", str(repo), "--json")

    output_html = repo / ".scratch" / "csegraph" / "graph.html"
    result = run_cli(
        "export",
        "--repo",
        str(repo),
        "--format",
        "html",
        "--output",
        str(output_html),
        "--json",
    )

    assert result["output_path"] == str(output_html.resolve())
    assert output_html.exists()


def test_graph_visual_export_rejects_tmp_output(tmp_path):
    # Negative path policy: CLI export --output must be repo-local, not OS temp.
    repo = tmp_path / "repo"
    _write_repo(repo)
    run_cli("index", str(repo), "--json")
    temp_output = Path(tempfile.gettempdir()) / f"{tmp_path.name}-csegraph-graph.html"

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "csegraph._cli",
            "export",
            "--repo",
            str(repo),
            "--format",
            "html",
            "--output",
            str(temp_output),
            "--json",
        ],
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 1


def test_inspect_resolves_folder_node(tmp_path):
    repo = tmp_path / "repo"
    _write_nested_repo(repo)
    run_cli("index", str(repo), "--json")

    result = run_cli(
        "inspect",
        "folder::pkg",
        "--repo",
        str(repo),
        "--depth",
        "1",
        "--detail-level",
        "standard",
        "--json",
    )
    assert result["command"] == "inspect"
    assert result["target"] == "folder::pkg"
    assert any(n["kind"] == "folder" for n in result["nodes"])
    assert any(edge["relation"] == "contains" for edge in result["edges"])


def test_inspect_resolves_folder_by_name(tmp_path):
    repo = tmp_path / "repo"
    _write_nested_repo(repo)
    run_cli("index", str(repo), "--json")

    result = run_cli(
        "inspect",
        "pkg",
        "--repo",
        str(repo),
        "--json",
    )
    assert result["target"] == "folder::pkg"


def test_inspect_resolves_repo_node(tmp_path):
    repo = tmp_path / "repo"
    _write_nested_repo(repo)
    run_cli("index", str(repo), "--json")

    result = run_cli(
        "inspect",
        "repo::repo",
        "--repo",
        str(repo),
        "--depth",
        "1",
        "--json",
    )
    assert result["command"] == "inspect"
    assert result["target"] == "repo::repo"
    assert any(n["kind"] == "repo" for n in result["nodes"])


def test_inspect_folder_includes_child_contains_edges(tmp_path):
    repo = tmp_path / "repo"
    _write_nested_repo(repo)
    run_cli("index", str(repo), "--json")

    result = run_cli(
        "inspect",
        "folder::pkg",
        "--repo",
        str(repo),
        "--depth",
        "1",
        "--detail-level",
        "standard",
        "--json",
    )
    contains_targets = [
        edge["target"] for edge in result["edges"] if edge["relation"] == "contains"
    ]
    assert "file::pkg/__init__.py" in contains_targets
    assert "file::pkg/worker.py" in contains_targets


def test_inspect_dot_resolves_to_repo_node(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    run_cli("index", str(repo), "--json")

    result = run_cli(
        "inspect",
        ".",
        "--repo",
        str(repo),
        "--json",
    )
    assert result["target"] == "repo::repo"


def test_inspect_repo_absolute_path_resolves_to_repo_node(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    run_cli("index", str(repo), "--json")

    result = run_cli(
        "inspect",
        str(repo),
        "--repo",
        str(repo),
        "--json",
    )
    assert result["target"] == "repo::repo"


def test_inspect_repo_basename_resolves_to_repo_node(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    run_cli("index", str(repo), "--json")

    result = run_cli(
        "inspect",
        "repo",
        "--repo",
        str(repo),
        "--json",
    )
    assert result["target"] == "repo::repo"
