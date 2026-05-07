"""CodegenService — generate Python code from a csegraph index.

Chains ContextService (retrieval + CSE) with an LLM backend
(local GGUF primary, Groq API fallback) to produce code in one call.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from csegraph_core.core.models import ContextNode, ContextResult
from csegraph_core.retrieval.context import ContextService
from csegraph_codegen.models import CodegenResult


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_TOKENS = 2048

# Cache loaded GGUF models across generate() calls within one process.
_LOCAL_LLM_CACHE: Dict[str, object] = {}


class CodegenService:
    """Generate code for a target symbol using graph-backed context.

    Parameters
    ----------
    db_path:
        Path to the csegraph SQLite index (e.g. ``<repo>/.csegraph/index.db``).
    groq_model:
        Groq model ID used when no local GGUF is available.
    model_path:
        Explicit path to a GGUF file.  Overrides auto-selection.
    model_dir:
        Directory scanned for GGUF files.  Overrides the
        ``GGUF_MODEL_DIR`` env var.
    temperature:
        LLM sampling temperature (default 0.2).
    max_tokens:
        Maximum tokens in the generated completion (default 2048).
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        groq_model: str = DEFAULT_GROQ_MODEL,
        model_path: Optional[str] = None,
        model_dir: Optional[str] = None,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self.db_path = str(Path(db_path))
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._local_llm: Any = None
        self._groq_client: Any = None
        self.model = groq_model

        if model_dir:
            os.environ["GGUF_MODEL_DIR"] = model_dir

        # Try explicit model path first, then auto-select.
        self._try_load_local(model_path)

        # Groq fallback.
        if self._local_llm is None:
            api_key = os.environ.get("GROQ_API_KEY", "")
            if not api_key:
                raise ValueError(
                    "No local GGUF model found and GROQ_API_KEY is not set. "
                    "Place a .gguf file in codermodel/ or set GROQ_API_KEY."
                )
            self._groq_client = _init_groq_client(api_key)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(
        self,
        task: str,
        *,
        target: Optional[str] = None,
        profile: str = "medium",
        output_path: Optional[str] = None,
    ) -> CodegenResult:
        """Retrieve context and generate code in one step.

        Parameters
        ----------
        task:
            Natural-language description of what to generate,
            e.g. ``"Add a calculator function"``.
        target:
            Optional target symbol name, node ID, or file path.
        profile:
            Retrieval profile — ``small``, ``medium``, or ``large``.
        output_path:
            If set, write the generated ``.py`` file to this path.

        Returns
        -------
        CodegenResult
            Dataclass with generated code, metrics, and metadata.
        """
        context = ContextService(self.db_path).build_context(
            task=task,
            target=target,
            profile=profile,
        )

        system_msg, user_msg = self._build_prompt(task, context)

        t0 = time.perf_counter()
        if self._local_llm is not None:
            generated_code, prompt_tokens, completion_tokens = (
                self._generate_local(system_msg, user_msg)
            )
        else:
            generated_code, prompt_tokens, completion_tokens = (
                self._generate_groq(system_msg, user_msg)
            )
        elapsed = time.perf_counter() - t0

        written_path: Optional[str] = None
        if output_path:
            written_path = str(Path(output_path).resolve())
            _write_code_file(written_path, generated_code, self.model, context)

        return CodegenResult(
            command="codegen",
            db_path=self.db_path,
            repo_root=context.repo_root,
            profile=context.profile,
            task=task,
            target_node_id=context.target_node_id,
            model=self.model,
            generated_code=generated_code,
            is_sufficient=context.is_sufficient,
            metrics=context.metrics,
            context_nodes_used=[n.node_id for n in context.context_nodes],
            raw_code_nodes_used=context.raw_code_nodes,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            elapsed_seconds=round(elapsed, 2),
            output_path=written_path,
        )

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    @staticmethod
    def _build_prompt(
        task: str, context: ContextResult
    ) -> tuple[str, str]:
        """Return (system_message, user_message) for the LLM call."""
        system_msg = (
            "You are an expert Python software engineer. "
            "You will be given compressed summaries and, for critical nodes, "
            "verbatim source code extracted from a repository. "
            "Use this context to generate a correct, complete Python implementation "
            "for the requested target. Output ONLY the Python code inside a single "
            "```python ... ``` block with no additional explanation."
        )

        summary_lines: List[str] = []
        raw_blocks: List[str] = []
        raw_set = set(context.raw_code_nodes)

        for node in context.context_nodes:
            if node.node_id in raw_set:
                # Verbatim source for raw-code nodes.
                src = _read_node_source(context.repo_root, node)
                if src:
                    raw_blocks.append(
                        f"# Source: {node.file_path} — {node.name}\n{src}"
                    )
            elif node.summary:
                summary_lines.append(
                    f"  [{node.kind}] {node.name} — {node.summary}"
                )

        parts: List[str] = [f"## Task\n{task}"]
        if context.target_node_id:
            target_node = next(
                (n for n in context.context_nodes if n.node_id == context.target_node_id),
                None,
            )
            if target_node:
                parts.append(f"## Target file\n{target_node.file_path}")

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

        if not context.is_sufficient:
            parts.append(
                "## Warning\n"
                "The Context Sufficiency Estimator flagged this context as "
                "potentially incomplete. Generate the best possible implementation "
                "given the available information."
            )

        return system_msg, "\n\n".join(parts)

    # ------------------------------------------------------------------
    # LLM backends
    # ------------------------------------------------------------------

    def _generate_local(
        self, system_msg: str, user_msg: str
    ) -> tuple[str, Optional[int], Optional[int]]:
        response = self._local_llm.create_chat_completion(
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )
        raw = response["choices"][0]["message"]["content"] or ""
        usage = response.get("usage", {})
        return (
            _extract_code_block(raw),
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
        )

    def _generate_groq(
        self, system_msg: str, user_msg: str
    ) -> tuple[str, Optional[int], Optional[int]]:
        response = self._groq_client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )
        raw = response.choices[0].message.content or ""
        usage = response.usage
        return (
            _extract_code_block(raw),
            usage.prompt_tokens if usage else None,
            usage.completion_tokens if usage else None,
        )

    # ------------------------------------------------------------------
    # Model loading
    # ------------------------------------------------------------------

    def _try_load_local(self, explicit_path: Optional[str]) -> None:
        """Attempt to load a local GGUF model via llama-cpp-python."""
        try:
            from llama_cpp import Llama  # type: ignore[import-untyped]

            # Import here to avoid hard dep on system_profile from the SDK.
            import sys
            import importlib.util

            # system_profile is repo-local, not part of the add-on package.
            # Find it when running from the source checkout; otherwise fall
            # through to Groq.
            sp_path = next(
                (
                    parent / "system_profile.py"
                    for parent in Path(__file__).resolve().parents
                    if (parent / "system_profile.py").exists()
                ),
                None,
            )
            if sp_path is None:
                return
            spec = importlib.util.spec_from_file_location("system_profile", sp_path)
            if spec is None or spec.loader is None:
                return
            sp = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(sp)

            profile = sp.build_system_profile()

            if explicit_path:
                gguf_path = explicit_path
                n_gpu_layers = profile.n_gpu_layers
            else:
                result = sp.select_gguf_model(profile)
                if not result:
                    return
                gguf_path, n_gpu_layers = result

            cache_key = (
                f"{gguf_path}|ctx=4096|threads={profile.n_threads}|gpu={n_gpu_layers}"
            )
            if cache_key in _LOCAL_LLM_CACHE:
                self._local_llm = _LOCAL_LLM_CACHE[cache_key]
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
        except Exception:
            pass  # Fall through to Groq


# ---------------------------------------------------------------------------
# Helpers (module-private)
# ---------------------------------------------------------------------------


def _extract_code_block(text: str) -> str:
    """Extract a fenced Python code block from LLM output."""
    # Strip think block — take everything after </think>.
    if "</think>" in text:
        text = text[text.index("</think>") + len("</think>") :].lstrip()

    match = re.search(r"```python\s*(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r"```\s*(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()


def _read_node_source(repo_root: str, node: ContextNode) -> str:
    """Read verbatim source lines for a context node."""
    if not node.file_path or node.start_line is None or node.end_line is None:
        return ""
    abs_path = os.path.join(repo_root, node.file_path)
    if not os.path.isfile(abs_path):
        return ""
    try:
        with open(abs_path, encoding="utf-8") as fh:
            lines = fh.readlines()
        start = max(0, node.start_line - 1)
        end = min(len(lines), node.end_line)
        return "".join(lines[start:end])
    except Exception:
        return ""


def _init_groq_client(api_key: str) -> Any:
    try:
        from groq import Groq  # type: ignore[import-untyped]

        return Groq(api_key=api_key)
    except ImportError as exc:
        raise ImportError(
            "groq package not found. Install it with:\n  pip install groq"
        ) from exc


def _write_code_file(
    output_path: str,
    generated_code: str,
    model: str,
    context: ContextResult,
) -> None:
    """Write the generated code to a .py file with a metadata header."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    header = (
        f"# Generated by csegraph codegen\n"
        f"# Model: {model}\n"
        f"# Target: {context.target_node_id}\n"
        f"# CSE: sufficient={context.is_sufficient}, "
        f"context_nodes={len(context.context_nodes)}, "
        f"raw_code_nodes={len(context.raw_code_nodes)}\n\n"
    )
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(header + generated_code + "\n")
