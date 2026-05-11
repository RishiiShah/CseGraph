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


def _run_cli(*args: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "csegraph_cli", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(proc.stdout)


def _run_cli_text(*args: str) -> str:
    proc = subprocess.run(
        [sys.executable, "-m", "csegraph_cli", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout


def test_report_json_contract(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    _run_cli("index", str(repo), "--json")

    result = _run_cli("report", str(repo), "--json")

    assert result["command"] == "report"
    assert result["total_files"] == 2
    assert result["total_symbols"] >= 2
    assert result["total_edges"] >= 1
    assert result["parse_error_count"] == 0
    assert isinstance(result["node_counts"], dict)
    assert "file" in result["node_counts"]
    assert isinstance(result["edge_counts"], dict)
    assert isinstance(result["god_nodes"], list)
    assert isinstance(result["knowledge_gaps"], list)
    assert isinstance(result["surprising_connections"], list)
    assert isinstance(result["suggested_questions"], list)


def test_report_json_god_nodes_are_sorted_by_degree(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    _run_cli("index", str(repo), "--json")

    result = _run_cli("report", str(repo), "--json")

    degrees = [n["degree"] for n in result["god_nodes"]]
    assert degrees == sorted(degrees, reverse=True)


def test_report_json_is_deterministic(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    _run_cli("index", str(repo), "--json")

    run1 = _run_cli("report", str(repo), "--json")
    run2 = _run_cli("report", str(repo), "--json")

    del run1["db_path"], run1["repo_root"]
    del run2["db_path"], run2["repo_root"]
    assert run1 == run2


def test_report_default_output_is_markdown(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)
    _run_cli("index", str(repo), "--json")

    output = _run_cli_text("report", str(repo))

    assert "# csegraph report" in output
    assert "## Corpus Check" in output
    assert "## Summary" in output
    assert "## God Nodes" in output
    assert "Files" in output
    assert "Symbols" in output
    assert "Edges" in output


def test_report_knowledge_gaps_contain_low_degree_symbols(tmp_path):
    repo = tmp_path / "repo"
    _write_repo(repo)

    (repo / "orphan.py").write_text(
        "def isolated_function():\n    return 42\n",
        encoding="utf-8",
    )

    _run_cli("index", str(repo), "--json")
    result = _run_cli("report", str(repo), "--json")

    gap_names = [n["name"] for n in result["knowledge_gaps"]]
    assert "isolated_function" in gap_names


def test_report_knowledge_gaps_exclude_noise(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "models.py").write_text(
        "class Thing:\n    def __init__(self):\n        self.x = 1\n",
        encoding="utf-8",
    )
    migrations = repo / "migrations"
    migrations.mkdir()
    (migrations / "__init__.py").write_text("", encoding="utf-8")
    (migrations / "m001.py").write_text(
        "def upgrade():\n    pass\n",
        encoding="utf-8",
    )
    (repo / "orphan.py").write_text(
        "def real_gap():\n    return 42\n",
        encoding="utf-8",
    )

    _run_cli("index", str(repo), "--json")
    result = _run_cli("report", str(repo), "--json")

    gap_names = [n["name"] for n in result["knowledge_gaps"]]
    assert "real_gap" in gap_names
    assert "__init__" not in gap_names
    assert "upgrade" not in gap_names


def test_report_sections_by_folder(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    core = repo / "core"
    core.mkdir()
    (core / "__init__.py").write_text("", encoding="utf-8")
    (core / "engine.py").write_text(
        "def compute():\n    return 1\n",
        encoding="utf-8",
    )
    utils = repo / "utils"
    utils.mkdir()
    (utils / "__init__.py").write_text("", encoding="utf-8")
    (utils / "helpers.py").write_text(
        "def fmt(x):\n    return str(x)\n",
        encoding="utf-8",
    )

    _run_cli("index", str(repo), "--json")
    result = _run_cli("report", str(repo), "--json")

    assert isinstance(result["sections"], list)
    section_names = [s["name"] for s in result["sections"]]
    assert "core" in section_names
    assert "utils" in section_names
    core_section = next(s for s in result["sections"] if s["name"] == "core")
    assert core_section["files"] >= 1
    assert core_section["symbols"] >= 1
    assert isinstance(core_section["internal_edges"], int)
    assert isinstance(core_section["cross_section_deps"], list)


def test_report_sections_in_markdown(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    pkg = repo / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "mod.py").write_text("def f():\n    pass\n", encoding="utf-8")

    _run_cli("index", str(repo), "--json")
    output = _run_cli_text("report", str(repo))

    assert "## Sections" in output
    assert "pkg" in output


def test_report_knowledge_gaps_exclude_class_qualified_dunders(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "models.py").write_text(
        "class Widget:\n"
        "    def __init__(self):\n"
        "        self.x = 1\n"
        "    def __post_init__(self):\n"
        "        pass\n",
        encoding="utf-8",
    )
    (repo / "orphan.py").write_text(
        "def real_gap():\n    return 42\n",
        encoding="utf-8",
    )

    _run_cli("index", str(repo), "--json")
    result = _run_cli("report", str(repo), "--json")

    gap_names = [n["name"] for n in result["knowledge_gaps"]]
    assert "real_gap" in gap_names
    for name in gap_names:
        bare = name.rsplit(".", 1)[-1] if "." in name else name
        assert not (bare.startswith("__") and bare.endswith("__")), (
            f"dunder method '{name}' should be filtered from knowledge gaps"
        )


def test_report_surprising_connections_deduped(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    a = repo / "alpha"
    a.mkdir()
    (a / "__init__.py").write_text("", encoding="utf-8")
    (a / "caller.py").write_text(
        "from beta.target import do_thing\n\n"
        "def run():\n    do_thing()\n    do_thing()\n",
        encoding="utf-8",
    )
    b = repo / "beta"
    b.mkdir()
    (b / "__init__.py").write_text("", encoding="utf-8")
    (b / "target.py").write_text(
        "def do_thing():\n    return 1\n",
        encoding="utf-8",
    )

    _run_cli("index", str(repo), "--json")
    result = _run_cli("report", str(repo), "--json")

    triples = [
        (c["source"], c["relation"], c["target"])
        for c in result["surprising_connections"]
    ]
    assert len(triples) == len(set(triples)), "surprising_connections has duplicates"


def test_report_with_custom_db(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "custom.db"
    _write_repo(repo)
    _run_cli("index", "--repo", str(repo), "--db", str(db_path), "--json")

    result = _run_cli("report", "--db", str(db_path), "--json")

    assert result["command"] == "report"
    assert result["total_files"] == 2
