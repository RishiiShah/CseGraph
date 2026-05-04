from __future__ import annotations

import json
import os
import sys
import time
from typing import Dict, List, Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.code_gen_result import CodeGenResult
from models.compressed_graph import CompressedGraph
from models.cse_result import SufficiencyResult
from models.link_graph import GraphNode, LinkGraph
from system_profile import build_system_profile, select_gguf_model


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
DEFAULT_TEMPERATURE = 0.2   # Low temperature for deterministic code output
DEFAULT_MAX_TOKENS = 2048

# Reuse local llama-cpp model instances across sandboxes in the same process.
# Keyed by (model path + runtime config) to avoid repeated heavy loads.
_LOCAL_LLM_CACHE: Dict[str, object] = {}


class CodeGenAgent:
    """Generates code via local GGUF model (primary) or Groq API (fallback).

    Parameters
    ----------
    link_graph_path:
        Path to the serialised LinkGraph JSON (needed for raw-code fetch).
    compressed_graph_path:
        Path to the serialised CompressedGraph JSON (summaries source).
    groq_model:
        Groq model ID used when no local GGUF is available.
    api_key:
        Groq API key.  Defaults to the ``GROQ_API_KEY`` environment variable.
        Only required when local model is not available.
    """

    def __init__(
        self,
        link_graph_path: str,
        compressed_graph_path: str,
        groq_model: str = DEFAULT_GROQ_MODEL,
        api_key: Optional[str] = None,
    ) -> None:
        self._local_llm = None
        self._groq_client = None
        self.model = groq_model  # updated to GGUF filename if local loads

        # Try local GGUF 
        try:
            from llama_cpp import Llama
            profile = build_system_profile()
            result = select_gguf_model(profile)
            if result:
                gguf_path, n_gpu_layers = result
                cache_key = (
                    f"{gguf_path}|ctx=4096|threads={profile.n_threads}|gpu={n_gpu_layers}"
                )
                if cache_key in _LOCAL_LLM_CACHE:
                    self._local_llm = _LOCAL_LLM_CACHE[cache_key]
                    print(f"[CodeGenAgent] Reusing local model: {os.path.basename(gguf_path)}")
                else:
                    self._local_llm = Llama(
                        model_path=gguf_path,
                        n_ctx=4096,
                        n_threads=profile.n_threads,
                        n_gpu_layers=n_gpu_layers,
                        verbose=False,
                    )
                    _LOCAL_LLM_CACHE[cache_key] = self._local_llm
                self.model = os.path.basename(gguf_path)
                print(f"[CodeGenAgent] Using local model: {self.model}")
        except Exception as exc:
            print(f"[CodeGenAgent] Local GGUF unavailable ({exc}), trying Groq.")

        # Groq fallback 
        if self._local_llm is None:
            api_key = api_key or os.environ.get("GROQ_API_KEY", "")
            if not api_key:
                raise ValueError(
                    "No local GGUF model found and GROQ_API_KEY is not set. "
                    "Place a .gguf file in codermodel/ or set GROQ_API_KEY."
                )
            self._groq_client = self._init_groq_client(api_key)
            print(f"[CodeGenAgent] Using Groq model: {self.model}")

        self._link_graph = self._load_link_graph(link_graph_path)
        self._compressed_graph = self._load_compressed_graph(compressed_graph_path)
        self._node_lookup: Dict[str, GraphNode] = {
            n.id: n for n in self._link_graph.nodes
        }

    # Public API
    def generate(self, cse_result: SufficiencyResult) -> CodeGenResult:
        """Generate code conditioned on a validated CSE result.

        If ``cse_result.is_sufficient`` is False the agent still attempts
        generation but flags ``cse_sufficient=False`` in the result so
        callers can decide how to handle it.
        """
        system_msg, user_msg = self._build_prompt(cse_result)
        if self._local_llm is not None:
            return self._generate_local(cse_result, system_msg, user_msg)
        return self._generate_groq(cse_result, system_msg, user_msg)

    def _generate_local(
        self,
        cse_result: SufficiencyResult,
        system_msg: str,
        user_msg: str,
    ) -> CodeGenResult:
        """Run inference on the local GGUF model via llama-cpp-python."""
        t0 = time.perf_counter()
        response = self._local_llm.create_chat_completion(
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user",   "content": user_msg},
            ],
            temperature=DEFAULT_TEMPERATURE,
            max_tokens=DEFAULT_MAX_TOKENS,
        )
        print(f"[CodeGenAgent] Local generation finished in {time.perf_counter() - t0:.1f}s")
        generated_code = response["choices"][0]["message"]["content"] or ""
        generated_code = self._extract_code_block(generated_code)

        usage = response.get("usage", {})
        return CodeGenResult(
            generated_code=generated_code,
            query_text=cse_result.query.query_text,
            target_node_id=cse_result.query.target_node_id,
            target_file_path=cse_result.query.target_file_path,
            model=self.model,
            context_nodes_used=list(cse_result.context_node_ids),
            raw_code_nodes_used=list(cse_result.raw_code_nodes),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            cse_sufficient=cse_result.is_sufficient,
            cse_rounds=cse_result.expansion_rounds,
            mean_logprob=None,
        )

    def _generate_groq(
        self,
        cse_result: SufficiencyResult,
        system_msg: str,
        user_msg: str,
    ) -> CodeGenResult:
        """Run inference via Groq API with optional logprob extraction."""
        mean_logprob: Optional[float] = None
        try:
            response = self._groq_client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user",   "content": user_msg},
                ],
                temperature=DEFAULT_TEMPERATURE,
                max_tokens=DEFAULT_MAX_TOKENS,
                logprobs=True,
            )
            try:
                tokens = response.choices[0].logprobs.content
                if tokens:
                    mean_logprob = sum(t.logprob for t in tokens) / len(tokens)
            except AttributeError:
                pass
        except Exception:
            response = self._groq_client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user",   "content": user_msg},
                ],
                temperature=DEFAULT_TEMPERATURE,
                max_tokens=DEFAULT_MAX_TOKENS,
            )

        generated_code = response.choices[0].message.content or ""
        generated_code = self._extract_code_block(generated_code)

        usage = response.usage
        return CodeGenResult(
            generated_code=generated_code,
            query_text=cse_result.query.query_text,
            target_node_id=cse_result.query.target_node_id,
            target_file_path=cse_result.query.target_file_path,
            model=self.model,
            context_nodes_used=list(cse_result.context_node_ids),
            raw_code_nodes_used=list(cse_result.raw_code_nodes),
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            cse_sufficient=cse_result.is_sufficient,
            cse_rounds=cse_result.expansion_rounds,
            mean_logprob=mean_logprob,
        )

    def save_result(self, result: CodeGenResult, output_path: str) -> None:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result.model_dump(), f, indent=4)
        print(f"Saved CodeGen result to '{output_path}'")

    def save_code(self, result: CodeGenResult, output_path: str) -> None:
        """Write only the generated source code to a .py file."""
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        header = (
            f"# Generated by CodeGenAgent\n"
            f"# Model: {result.model}\n"
            f"# Target: {result.target_node_id}\n"
            f"# File: {result.target_file_path}\n"
            f"# CSE: sufficient={result.cse_sufficient}, "
            f"rounds={result.cse_rounds}, "
            f"context_nodes={len(result.context_nodes_used)}, "
            f"raw_code_nodes={len(result.raw_code_nodes_used)}\n"
            f"# Tokens  : prompt={result.prompt_tokens}, "
            f"completion={result.completion_tokens}\n\n"
        )
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(header + result.generated_code + "\n")
        print(f"Saved generated code to '{output_path}'")

    @staticmethod
    def find_test_file(target_file_path: str, repo_root: str) -> Optional[str]:
        """Return the path to the test file for *target_file_path*, or None.

        Searches candidates in order:
          1. ``<repo_root>/tests/test_<module>.py``
          2. ``<repo_root>/test_<module>.py``
          3. ``<same_dir_as_target>/test_<module>.py``
        """
        module_name = os.path.splitext(os.path.basename(target_file_path))[0]
        target_dir = os.path.dirname(os.path.join(repo_root, target_file_path))
        candidates = [
            os.path.join(repo_root, "tests", f"test_{module_name}.py"),
            os.path.join(repo_root, f"test_{module_name}.py"),
            os.path.join(target_dir, f"test_{module_name}.py"),
        ]
        for path in candidates:
            if os.path.isfile(path):
                return path
        return None

    def generate_tests(
        self,
        codegen_result: CodeGenResult,
        existing_test_content: Optional[str] = None,
    ) -> str:
        """Generate or update a pytest test file for *codegen_result.generated_code*.
        Returns
        -------
        str
            Python source code for the test file, extracted from the LLM
            response.
        """
        system_msg = (
            "You are an expert Python test engineer. "
            "You will be given Python source code that was just generated or modified. "
            "Write comprehensive pytest unit tests for it. "
            "If an existing test file is provided, update it to cover new or changed "
            "behaviour while keeping all previously valid tests intact. "
            "Use fixtures where appropriate. "
            "Output ONLY the test code inside a single ```python ... ``` block "
            "with no additional explanation."
        )

        parts: List[str] = [
            f"## Module under test\n`{codegen_result.target_file_path}`",
            f"## Generated source code\n```python\n{codegen_result.generated_code}\n```",
        ]
        if existing_test_content:
            parts.append(
                "## Existing test file (update this, preserve passing tests)\n"
                f"```python\n{existing_test_content}\n```"
            )
        else:
            parts.append(
                "## Note\nNo existing test file found — write tests from scratch."
            )

        user_msg = "\n\n".join(parts)

        if self._local_llm is not None:
            response = self._local_llm.create_chat_completion(
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user",   "content": user_msg},
                ],
                temperature=DEFAULT_TEMPERATURE,
                max_tokens=DEFAULT_MAX_TOKENS,
            )
            raw = response["choices"][0]["message"]["content"] or ""
        else:
            response = self._groq_client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user",   "content": user_msg},
                ],
                temperature=DEFAULT_TEMPERATURE,
                max_tokens=DEFAULT_MAX_TOKENS,
            )
            raw = response.choices[0].message.content or ""

        return self._extract_code_block(raw)

    # Prompt construction
    def _build_prompt(self, cse_result: SufficiencyResult):
        """Return (system_message, user_message) for the LLM call."""
        query = cse_result.query

        system_msg = (
            "You are an expert Python software engineer. "
            "You will be given compressed summaries and, for critical nodes, "
            "verbatim source code extracted from a repository. "
            "Use this context to generate a correct, complete Python implementation "
            "for the requested target. Output ONLY the Python code inside a single "
            "```python ... ``` block with no additional explanation."
        )

        # Context: compressed summaries
        summary_lines: List[str] = []
        raw_code_set = set(cse_result.raw_code_nodes)
        for node_id in cse_result.context_node_ids:
            if node_id in raw_code_set:
                continue
            if node_id in self._compressed_graph.node_summaries:
                ns = self._compressed_graph.node_summaries[node_id]
                summary_lines.append(f"  [{ns.node_type}] {ns.name} — {ns.summary}")

        # Context: verbatim raw code for low-confidence nodes
        raw_blocks: List[str] = []
        for node_id in cse_result.raw_code_nodes:
            node = self._node_lookup.get(node_id)
            if node is None:
                continue
            raw = self._fetch_raw_code(node)
            if raw:
                raw_blocks.append(
                    f"# Source: {node.file_path} — {node.name}\n{raw}"
                )

        # Assemble user message
        parts: List[str] = []
        parts.append(f"## Task\n{query.query_text}")
        parts.append(f"## Target file\n{query.target_file_path}")

        if summary_lines:
            parts.append(
                "## Repository context (compressed summaries)\n"
                + "\n".join(summary_lines)
            )

        if raw_blocks:
            parts.append(
                "## Critical dependencies (verbatim source)\n"
                + "\n\n".join(raw_blocks)
            )

        if not cse_result.is_sufficient:
            parts.append(
                "## Warning\n"
                "The Context Sufficiency Estimator flagged this context as "
                "potentially incomplete. Generate the best possible implementation "
                "given the available information."
            )

        user_msg = "\n\n".join(parts)
        return system_msg, user_msg

    # Helpers
    def _fetch_raw_code(self, node: GraphNode) -> str:
        """Read verbatim source lines for a node using its line range."""
        if not node.file_path or node.start_line is None or node.end_line is None:
            return ""
        abs_path = os.path.join(self._link_graph.root_dir, node.file_path)
        if not os.path.isfile(abs_path):
            return ""
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            start = max(0, node.start_line - 1)
            end = min(len(lines), node.end_line)
            return "".join(lines[start:end])
        except Exception:
            return ""

    @staticmethod
    def _extract_code_block(text: str) -> str:
        import re
        # Strip think block — take everything after the first </think>
        if "</think>" in text:
            text = text[text.index("</think>") + len("</think>"):].lstrip()

        match = re.search(r"```python\s*(.*?)```", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        match = re.search(r"```\s*(.*?)```", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return text.strip()

    @staticmethod
    def _init_groq_client(api_key: str):
        try:
            from groq import Groq
            return Groq(api_key=api_key)
        except ImportError:
            raise ImportError(
                "groq package not found. Install it with:\n"
                "  pip install groq"
            )

    @staticmethod
    def _load_link_graph(path: str) -> LinkGraph:
        with open(path, "r", encoding="utf-8") as f:
            return LinkGraph(**json.load(f))

    @staticmethod
    def _load_compressed_graph(path: str) -> CompressedGraph:
        with open(path, "r", encoding="utf-8") as f:
            return CompressedGraph(**json.load(f))


# CLI entry point

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Run the Code Generation Agent on a CSE result."
    )
    parser.add_argument(
        "--link-graph",
        default="data/link_graph.json",
        help="Path to link_graph.json",
    )
    parser.add_argument(
        "--compressed-graph",
        default="data/compressed_graph.json",
        help="Path to compressed_graph.json",
    )
    parser.add_argument(
        "--cse-result",
        default="data/cse_result.json",
        help="Path to cse_result.json produced by the CSE agent",
    )
    parser.add_argument(
        "--output",
        default="data/code_gen_result.json",
        help="Output path for the generated code result",
    )
    parser.add_argument(
        "--groq-model",
        default=DEFAULT_GROQ_MODEL,
        help=f"Groq model ID used as fallback (default: {DEFAULT_GROQ_MODEL})",
    )

    args = parser.parse_args()

    with open(args.cse_result, "r", encoding="utf-8") as f:
        cse_result = SufficiencyResult(**json.load(f))

    if not cse_result.is_sufficient:
        print(
            f"Warning: CSE marked context as insufficient "
            f"(reason: {cse_result.reason}). Generating anyway."
        )

    agent = CodeGenAgent(args.link_graph, args.compressed_graph, groq_model=args.groq_model)
    result = agent.generate(cse_result)
    agent.save_result(result, args.output)

    print(f"\nModel: {result.model}")
    print(f"Target : {result.target_node_id}")
    print(f"CSE sufficient: {result.cse_sufficient}")
    print(f"CSE rounds: {result.cse_rounds}")
    print(f"Context nodes used : {len(result.context_nodes_used)}")
    print(f"Raw code nodes: {len(result.raw_code_nodes_used)}")
    print(f"Prompt tokens: {result.prompt_tokens}")
    print(f"Completion tokens: {result.completion_tokens}")
    print(f"\n--- Generated Code ---\n{result.generated_code}")
