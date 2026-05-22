from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from csegraph_core.server.app import _PROMPTS, _TOOLS


ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _root_runtime_dependency_names() -> set[str]:
    pyproject = _read("pyproject.toml")
    match = re.search(r"dependencies = \[(?P<body>.*?)\]\n", pyproject, re.S)
    assert match, "root pyproject.toml must define runtime dependencies"
    return set(re.findall(r'"([A-Za-z0-9_.-]+)', match.group("body")))


def _readme_base_command_lines() -> list[str]:
    readme = _read("README.md")
    match = re.search(r"## Base Commands\n\n```bash\n(?P<body>.*?)\n```", readme, re.S)
    assert match, "README.md must keep a bash Base Commands block"
    return [
        line.split("#", 1)[0].strip()
        for line in match.group("body").splitlines()
        if line.strip().startswith("csegraph ")
    ]


def _readme_source_install_lines() -> list[str]:
    readme = _read("README.md")
    match = re.search(r"## Install From Source\n\n```bash\n(?P<body>.*?)\n```", readme, re.S)
    assert match, "README.md must keep a bash Install From Source block"
    return [
        line.strip()
        for line in match.group("body").splitlines()
        if line.strip().startswith("env/bin/pip install -e ")
    ]


def test_readme_base_commands_are_real_cli_commands():
    help_proc = subprocess.run(
        [sys.executable, "-m", "csegraph_cli", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    command_lines = _readme_base_command_lines()

    assert command_lines
    for line in command_lines:
        command = line.split()[1]
        assert command in help_proc.stdout
    assert not any(
        line.startswith(f"csegraph {command}")
        for command in ("build", "update")
        for line in command_lines
    )
    assert not any(line.startswith("csegraph index .") for line in command_lines)
    assert not any(line.startswith("csegraph refresh .") for line in command_lines)


def test_requirements_txt_matches_readme_source_install_order():
    readme_installs = _readme_source_install_lines()
    requirements_installs = [
        f"env/bin/pip install {line}"
        for line in _read("requirements.txt").splitlines()
        if line.strip()
    ]

    assert requirements_installs == readme_installs


def test_base_commands_expose_help_from_source_install():
    base_commands = [
        "index",
        "refresh",
        "context",
        "status",
        "postprocess",
        "install",
        "serve",
    ]

    for command in base_commands:
        proc = subprocess.run(
            [sys.executable, "-m", "csegraph_cli", command, "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        assert f"usage: csegraph {command}" in proc.stdout


def test_documented_base_command_dependencies_are_runtime_dependencies():
    dependency_names = _root_runtime_dependency_names()

    assert {"mcp", "watchfiles", "tomlkit"}.issubset(dependency_names)


def test_watch_dependency_message_matches_base_install_contract():
    watch_source = _read("csegraph_core/watch.py")

    assert "csegraph-core[watch]" not in watch_source


def test_documented_mcp_tools_match_server_registry():
    readme = _read("README.md")
    tool_names = {tool.name for tool in _TOOLS}

    documented_readme = set(re.findall(r"\| `(csegraph_[a-z_]+)` \|", readme))

    assert documented_readme == tool_names
    assert ("csegraph_" + "minimal_context") not in readme


def test_documented_mcp_prompts_match_server_registry():
    readme = _read("README.md")
    prompt_names = {prompt.name for prompt in _PROMPTS}

    prompt_table = readme.split("| Prompt | Workflow |", 1)[1]
    documented_readme = set(re.findall(r"\| `(csegraph-[a-z-]+)` \|", prompt_table))

    assert documented_readme == prompt_names


def test_docs_paths_remain_ignored():
    docs_paths = [
        Path("docs") / ("AGENT" + "_REFERENCE.md"),
        Path("docs") / ("csegraph" + ".md"),
        Path("docs") / "example.md",
    ]

    for path in docs_paths:
        proc = subprocess.run(
            ["git", "check-ignore", "-q", str(path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, f"{path} must remain gitignored"
