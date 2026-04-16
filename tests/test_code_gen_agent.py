"""Tests for CodeGenAgent helper methods: find_test_file and generate_tests.

LLM calls in generate_tests are mocked so these tests run without API keys
or local GGUF models.
"""

import json
import os
import sys
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from agents.code_gen_agent import CodeGenAgent
from models.code_gen_result import CodeGenResult
from models.compressed_graph import CompressedGraph
from models.link_graph import LinkGraph


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_agent(tmp_path):
    """Return a CodeGenAgent with the LLM backend replaced by a MagicMock.

    Uses object.__new__ to bypass __init__ entirely and manually sets every
    attribute the public methods need.
    """
    lg_data = {
        "root_dir": str(tmp_path),
        "summary": {"file_count": 1, "symbol_count": 1, "edge_count": 0},
        "nodes": [
            {
                "id": "file::main.py",
                "type": "file",
                "name": "main.py",
                "file_path": "main.py",
            }
        ],
        "edges": [],
    }
    cg_data = {
        "root_dir": str(tmp_path),
        "original_graph_size": {"file_count": 1, "symbol_count": 1, "edge_count": 0},
        "node_summaries": {},
        "high_degree_nodes": [],
        "context_slices": {},
    }

    agent = object.__new__(CodeGenAgent)
    agent._local_llm = None
    agent._groq_client = MagicMock()
    agent.model = "mock-model"
    agent._link_graph = LinkGraph(**lg_data)
    agent._compressed_graph = CompressedGraph(**cg_data)
    agent._node_lookup = {}
    return agent


def _make_codegen_result(
    code: str = "def hello(): return 'hi'",
    target_file: str = "main.py",
) -> CodeGenResult:
    return CodeGenResult(
        generated_code=code,
        query_text="generate hello function",
        target_node_id="sym::main.py::function::hello",
        target_file_path=target_file,
        model="mock",
        cse_sufficient=True,
        cse_rounds=1,
    )


def _set_mock_groq_response(agent: CodeGenAgent, content: str) -> None:
    """Configure agent._groq_client to return *content* as message text."""
    choice = MagicMock()
    choice.message.content = f"```python\n{content}\n```"
    agent._groq_client.chat.completions.create.return_value.choices = [choice]


# ---------------------------------------------------------------------------
# Tests: find_test_file (pure filesystem — no LLM needed)
# ---------------------------------------------------------------------------


class TestFindTestFile:
    def test_finds_tests_subdirectory(self, tmp_path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_mymodule.py").write_text("# tests")
        result = CodeGenAgent.find_test_file("mymodule.py", str(tmp_path))
        assert result == str(tests_dir / "test_mymodule.py")

    def test_finds_root_level_test_file(self, tmp_path):
        (tmp_path / "test_mymodule.py").write_text("# tests")
        result = CodeGenAgent.find_test_file("mymodule.py", str(tmp_path))
        assert result == str(tmp_path / "test_mymodule.py")

    def test_returns_none_when_not_found(self, tmp_path):
        result = CodeGenAgent.find_test_file("missing.py", str(tmp_path))
        assert result is None

    def test_prefers_tests_subdir_over_root_level(self, tmp_path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_mod.py").write_text("# subdir")
        (tmp_path / "test_mod.py").write_text("# root")
        result = CodeGenAgent.find_test_file("mod.py", str(tmp_path))
        assert result == str(tests_dir / "test_mod.py")

    def test_strips_extension_from_target(self, tmp_path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_service.py").write_text("# tests")
        result = CodeGenAgent.find_test_file("service.py", str(tmp_path))
        assert result == str(tests_dir / "test_service.py")

    def test_handles_nested_target_path(self, tmp_path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_cse_agent.py").write_text("# tests")
        result = CodeGenAgent.find_test_file("agents/cse_agent.py", str(tmp_path))
        assert result == str(tests_dir / "test_cse_agent.py")

    def test_falls_back_to_same_dir_as_target(self, tmp_path):
        sub = tmp_path / "agents"
        sub.mkdir()
        (sub / "test_service.py").write_text("# tests")
        # no tests/ subdir, no root test file
        result = CodeGenAgent.find_test_file("agents/service.py", str(tmp_path))
        assert result == str(sub / "test_service.py")

    def test_no_match_when_only_wrong_name_exists(self, tmp_path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_other.py").write_text("# unrelated")
        result = CodeGenAgent.find_test_file("mymodule.py", str(tmp_path))
        assert result is None


# ---------------------------------------------------------------------------
# Tests: generate_tests (Groq backend mocked)
# ---------------------------------------------------------------------------


class TestGenerateTests:
    def test_returns_extracted_code(self, mock_agent):
        _set_mock_groq_response(mock_agent, "def test_hello():\n    assert hello() == 'hi'")
        result = _make_codegen_result()
        test_code = mock_agent.generate_tests(result)
        assert "test_hello" in test_code

    def test_generated_code_appears_in_user_prompt(self, mock_agent):
        _set_mock_groq_response(mock_agent, "def test_x(): pass")
        result = _make_codegen_result("def foo(): return 42")
        mock_agent.generate_tests(result)
        call_args = mock_agent._groq_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        user_msg = next(m["content"] for m in messages if m["role"] == "user")
        assert "return 42" in user_msg

    def test_target_file_path_in_user_prompt(self, mock_agent):
        _set_mock_groq_response(mock_agent, "def test_x(): pass")
        result = _make_codegen_result(target_file="agents/cse_agent.py")
        mock_agent.generate_tests(result)
        call_args = mock_agent._groq_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        user_msg = next(m["content"] for m in messages if m["role"] == "user")
        assert "agents/cse_agent.py" in user_msg

    def test_existing_tests_included_in_prompt(self, mock_agent):
        _set_mock_groq_response(mock_agent, "def test_x(): pass")
        result = _make_codegen_result()
        mock_agent.generate_tests(result, existing_test_content="def test_old(): pass")
        call_args = mock_agent._groq_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        user_msg = next(m["content"] for m in messages if m["role"] == "user")
        assert "test_old" in user_msg

    def test_from_scratch_note_when_no_existing_tests(self, mock_agent):
        _set_mock_groq_response(mock_agent, "def test_x(): pass")
        result = _make_codegen_result()
        mock_agent.generate_tests(result, existing_test_content=None)
        call_args = mock_agent._groq_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        user_msg = next(m["content"] for m in messages if m["role"] == "user")
        assert "scratch" in user_msg.lower()

    def test_system_message_mentions_pytest(self, mock_agent):
        _set_mock_groq_response(mock_agent, "def test_x(): pass")
        result = _make_codegen_result()
        mock_agent.generate_tests(result)
        call_args = mock_agent._groq_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        sys_msg = next(m["content"] for m in messages if m["role"] == "system")
        assert "pytest" in sys_msg.lower()

    def test_strips_markdown_fences_from_response(self, mock_agent):
        _set_mock_groq_response(mock_agent, "import pytest\ndef test_foo(): pass")
        result = _make_codegen_result()
        test_code = mock_agent.generate_tests(result)
        # _extract_code_block should strip the ``` fences
        assert "```" not in test_code
        assert "test_foo" in test_code

    def test_groq_api_called_once(self, mock_agent):
        _set_mock_groq_response(mock_agent, "def test_x(): pass")
        result = _make_codegen_result()
        mock_agent.generate_tests(result)
        assert mock_agent._groq_client.chat.completions.create.call_count == 1
