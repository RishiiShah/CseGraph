from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from csegraph._core.server.app import _PROMPTS, _TOOLS

ROOT = Path(__file__).resolve().parents[2]

CORE_CONTEXT_COMMANDS = {
    "index",
    "refresh",
    "context",
    "path",
    "inspect",
    "serve",
}

SUPPORT_COMMANDS = {
    "export",
    "install",
    "watch",
    "lsp",
    "status",
    "postprocess",
}

PUBLIC_OPERATIONS_COMMANDS = {
    "registry",
    "daemon",
}

DIAGNOSTIC_BRIDGE_COMMANDS = {
    "analyze",
}

EXPECTED_PUBLIC_COMMANDS = (
    CORE_CONTEXT_COMMANDS
    | SUPPORT_COMMANDS
    | PUBLIC_OPERATIONS_COMMANDS
    | DIAGNOSTIC_BRIDGE_COMMANDS
)

DEV_ONLY_DIAGNOSTIC_COMMANDS = {
    "architecture",
    "flows",
    "resolvers",
    "communities",
    "report",
    "detect-changes",
    "test-gaps",
    "review-questions",
    "review-eval",
    "vulnerabilities",
    "embeddings",
    "benchmark",
}


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _help_commands(command: list[str]) -> set[str]:
    proc = subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    match = re.search(r"\{(?P<commands>[a-z0-9_,.-]+)\}", proc.stdout)
    assert match, proc.stdout
    return set(match.group("commands").split(","))


def _root_runtime_dependency_names() -> set[str]:
    pyproject = _read("pyproject.toml")
    match = re.search(r"dependencies = \[(?P<body>.*?)\]\n", pyproject, re.S)
    assert match, "root pyproject.toml must define runtime dependencies"
    return set(re.findall(r'"([A-Za-z0-9_.-]+)', match.group("body")))


def _command_reference_setup_lines() -> list[str]:
    reference = _read("docs/csegraph.md")
    match = re.search(r"## Setup\n\n```bash\n(?P<body>.*?)\n```", reference, re.S)
    assert match, "docs/csegraph.md must keep a bash Setup block"
    return [
        line.split("#", 1)[0].strip().replace("env/bin/", "")
        for line in match.group("body").splitlines()
        if "csegraph " in line
    ]


def _contributing_source_install_lines() -> list[str]:
    contributing = _read("CONTRIBUTING.md")
    match = re.search(r"## Development Setup\n\n```bash\n(?P<body>.*?)\n```", contributing, re.S)
    assert match, "CONTRIBUTING.md must keep a bash Development Setup block"
    return [
        line.strip()
        for line in match.group("body").splitlines()
        if line.strip().startswith("python -m pip install -e ")
    ]


def test_readme_base_commands_are_real_cli_commands():
    help_proc = subprocess.run(
        [sys.executable, "-m", "csegraph._cli", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    command_lines = _command_reference_setup_lines()

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


def test_source_install_stays_in_contributing_and_targets_root_package():
    contributing_installs = _contributing_source_install_lines()
    requirements = [line.strip() for line in _read("requirements.txt").splitlines() if line.strip()]

    assert "## Install From Source" not in _read("README.md")
    assert requirements == ["-e ."]
    assert contributing_installs
    assert all(re.search(r""" -e ["']?\.""", line) for line in contributing_installs)


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
            [sys.executable, "-m", "csegraph._cli", command, "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        assert f"usage: csegraph {command}" in proc.stdout


def test_public_cli_surface_stays_within_context_engine_boundary():
    public_commands = _help_commands([sys.executable, "-m", "csegraph._cli", "--help"])

    assert public_commands == EXPECTED_PUBLIC_COMMANDS
    assert DEV_ONLY_DIAGNOSTIC_COMMANDS.isdisjoint(public_commands)


def test_maintainer_diagnostics_stay_behind_dev_cli():
    dev_commands = _help_commands([sys.executable, "tools/csegraph_dev.py", "--help"])

    assert DEV_ONLY_DIAGNOSTIC_COMMANDS <= dev_commands


def test_documented_base_command_dependencies_are_runtime_dependencies():
    dependency_names = _root_runtime_dependency_names()

    assert {"mcp", "watchfiles", "tomlkit"}.issubset(dependency_names)


def test_documented_mcp_tools_match_server_registry():
    command_reference = _read("docs/csegraph.md")
    tool_names = {tool.name for tool in _TOOLS}

    documented_reference = set(re.findall(r"\| `(csegraph_[a-z_]+)` \|", command_reference))

    assert documented_reference == tool_names
    assert ("csegraph_" + "minimal_context") not in command_reference


def test_documented_mcp_prompts_match_server_registry():
    command_reference = _read("docs/csegraph.md")
    prompt_names = {prompt.name for prompt in _PROMPTS}

    prompt_table = command_reference.split("| Prompt | Workflow |", 1)[1]
    documented_reference = set(re.findall(r"\| `(csegraph-[a-z-]+)` \|", prompt_table))

    assert documented_reference == prompt_names


def test_public_docs_are_not_gitignored():
    for rel_path in ("docs/csegraph.md", "docs/architecture.md"):
        proc = subprocess.run(
            ["git", "check-ignore", "-q", rel_path],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert proc.returncode != 0, f"{rel_path} must be tracked for open source"


def test_local_only_paths_remain_gitignored():
    ignored_paths = ["learn.md", "ref/", "CLAUDE.md", "AGENTS.md"]
    for rel_path in ignored_paths:
        proc = subprocess.run(
            ["git", "check-ignore", "-q", rel_path],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, f"{rel_path} must remain gitignored"
