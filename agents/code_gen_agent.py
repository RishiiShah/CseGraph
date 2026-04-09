"""Code Generation Agent — code_gen_agent.py

Takes a validated SufficiencyResult from the CSE and uses a Groq-hosted LLM
to generate Python source code conditioned on the verified context.

Flow
----
1. Receive SufficiencyResult (must have is_sufficient=True to proceed).
2. Assemble the prompt:
   - System role: code generation assistant with context-awareness instructions.
   - Context block: compressed summaries for all context_node_ids.
   - Raw code block: verbatim source for raw_code_nodes (low-confidence nodes).
   - Task: the original query_text.
3. Call Groq API with the chosen model.
4. Return CodeGenResult with generated code + usage metadata.

Environment
-----------
Set GROQ_API_KEY in your environment before running:
    export GROQ_API_KEY=<your_key>          (Linux/Mac)
    set GROQ_API_KEY=<your_key>             (Windows CMD)
    $env:GROQ_API_KEY="<your_key>"          (PowerShell)
"""

from __future__ import annotations

import json
import os
import sys
from typing import Dict, List, Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.code_gen_result import CodeGenResult
from models.compressed_graph import CompressedGraph
from models.cse_result import SufficiencyResult
from models.link_graph import GraphNode, LinkGraph


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "llama-3.3-70b-versatile"
DEFAULT_TEMPERATURE = 0.2   # Low temperature for deterministic code output
DEFAULT_MAX_TOKENS = 2048


class CodeGenAgent:
    """Generates code using a Groq-hosted LLM, gated by the CSE.

    Parameters
    ----------
    link_graph_path:
        Path to the serialised LinkGraph JSON (needed for raw-code fetch).
    compressed_graph_path:
        Path to the serialised CompressedGraph JSON (summaries source).
    model:
        Groq model ID.  Default: ``llama-3.3-70b-versatile``.
    api_key:
        Groq API key.  Defaults to the ``GROQ_API_KEY`` environment variable.
    """

    def __init__(
        self,
        link_graph_path: str,
        compressed_graph_path: str,
        model: str = DEFAULT_MODEL,
        api_key: Optional[str] = None,
    ) -> None:
        self.model = model
        self._api_key = api_key or os.environ.get("GROQ_API_KEY", "")
        if not self._api_key:
            raise ValueError(
                "Groq API key not found. "
                "Set the GROQ_API_KEY environment variable or pass api_key=."
            )

        self._client = self._init_groq_client()
        self._link_graph = self._load_link_graph(link_graph_path)
        self._compressed_graph = self._load_compressed_graph(compressed_graph_path)
        self._node_lookup: Dict[str, GraphNode] = {
            n.id: n for n in self._link_graph.nodes
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self, cse_result: SufficiencyResult) -> CodeGenResult:
        """Generate code conditioned on a validated CSE result.

        If ``cse_result.is_sufficient`` is False the agent still attempts
        generation but flags ``cse_sufficient=False`` in the result so
        callers can decide how to handle it.
        """
        prompt_blocks = self._build_prompt(cse_result)
        system_msg, user_msg = prompt_blocks

        response = self._client.chat.completions.create(
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
            f"# Model   : {result.model}\n"
            f"# Target  : {result.target_node_id}\n"
            f"# File    : {result.target_file_path}\n"
            f"# CSE     : sufficient={result.cse_sufficient}, "
            f"rounds={result.cse_rounds}, "
            f"context_nodes={len(result.context_nodes_used)}, "
            f"raw_code_nodes={len(result.raw_code_nodes_used)}\n"
            f"# Tokens  : prompt={result.prompt_tokens}, "
            f"completion={result.completion_tokens}\n\n"
        )
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(header + result.generated_code + "\n")
        print(f"Saved generated code to '{output_path}'")

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

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

        # --- Context: compressed summaries ---
        summary_lines: List[str] = []
        raw_code_set = set(cse_result.raw_code_nodes)
        for node_id in cse_result.context_node_ids:
            if node_id in raw_code_set:
                continue  # raw code handled below
            if node_id in self._compressed_graph.node_summaries:
                ns = self._compressed_graph.node_summaries[node_id]
                summary_lines.append(f"  [{ns.node_type}] {ns.name} — {ns.summary}")

        # --- Context: verbatim raw code for low-confidence nodes ---
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

        # --- Assemble user message ---
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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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
        """Extract the first ```python ... ``` block from the LLM response.

        Falls back to the full response if no fenced block is found.
        """
        import re
        match = re.search(r"```python\s*(.*?)```", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        # Try generic ``` block
        match = re.search(r"```\s*(.*?)```", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return text.strip()

    def _init_groq_client(self):
        try:
            from groq import Groq
            return Groq(api_key=self._api_key)
        except ImportError:
            raise ImportError(
                "groq package not found. Install it with:\n"
                "  venv/Scripts/pip install groq"
            )

    @staticmethod
    def _load_link_graph(path: str) -> LinkGraph:
        with open(path, "r", encoding="utf-8") as f:
            return LinkGraph(**json.load(f))

    @staticmethod
    def _load_compressed_graph(path: str) -> CompressedGraph:
        with open(path, "r", encoding="utf-8") as f:
            return CompressedGraph(**json.load(f))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

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
        "--model",
        default=DEFAULT_MODEL,
        help=f"Groq model ID (default: {DEFAULT_MODEL})",
    )

    args = parser.parse_args()

    with open(args.cse_result, "r", encoding="utf-8") as f:
        cse_result = SufficiencyResult(**json.load(f))

    if not cse_result.is_sufficient:
        print(
            f"Warning: CSE marked context as insufficient "
            f"(reason: {cse_result.reason}). Generating anyway."
        )

    agent = CodeGenAgent(args.link_graph, args.compressed_graph, model=args.model)
    result = agent.generate(cse_result)
    agent.save_result(result, args.output)

    print(f"\nModel              : {result.model}")
    print(f"Target             : {result.target_node_id}")
    print(f"CSE sufficient     : {result.cse_sufficient}")
    print(f"CSE rounds         : {result.cse_rounds}")
    print(f"Context nodes used : {len(result.context_nodes_used)}")
    print(f"Raw code nodes     : {len(result.raw_code_nodes_used)}")
    print(f"Prompt tokens      : {result.prompt_tokens}")
    print(f"Completion tokens  : {result.completion_tokens}")
    print(f"\n--- Generated Code ---\n{result.generated_code}")
