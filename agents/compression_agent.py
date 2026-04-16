import ast
import hashlib
import json
import os
import sys
import textwrap
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

# Add parent directory so running from agents/ works without PYTHONPATH.
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.compressed_graph import CompressedGraph, ContextSlice, NodeSummary
from models.link_graph import LinkGraph, GraphEdge, GraphNode
from system_profile import SystemProfile, build_system_profile, select_gguf_model


class CompressionAgent:
    """Compresses a LinkGraph into a memory-aware representation."""

    # Prompt shared by all LLM backends
    _SUMMARIZE_PROMPT = (
        "Given this Python {node_type} named '{name}', "
        "write a single concise sentence describing what it does:\n\n"
        "```python\n{source}\n```\n\n"
        "Respond with only the one-sentence description, no code, no preamble."
    )

    def __init__(
        self,
        graph_path: str,
        use_llm: bool = False,
        gguf_model_path: Optional[str] = None,
        summary_cache_path: str = "data/summary_cache.json",
        groq_api_key: Optional[str] = None,
    ):
        self.graph_path = graph_path
        self._use_llm = use_llm
        self._gguf_model_path = gguf_model_path or os.environ.get("GGUF_MODEL_PATH", "")
        self._summary_cache_path = summary_cache_path
        self._groq_api_key = groq_api_key if groq_api_key is not None else os.environ.get("GROQ_API_KEY", "")

        self.graph: LinkGraph = self._load_graph(graph_path)
        self.root_dir = self.graph.root_dir

        # Build adjacency structures for efficient traversal
        self._outgoing: Dict[str, List[str]] = defaultdict(list)
        self._incoming: Dict[str, List[str]] = defaultdict(list)
        self._node_lookup: Dict[str, GraphNode] = {}

        for node in self.graph.nodes:
            self._node_lookup[node.id] = node

        for edge in self.graph.edges:
            self._outgoing[edge.source].append(edge.target)
            self._incoming[edge.target].append(edge.source)

        # Index file → contained symbol IDs (used for file-node summaries)
        self._file_contains: Dict[str, List[str]] = defaultdict(list)
        for edge in self.graph.edges:
            if edge.relation == "contains":
                self._file_contains[edge.source].append(edge.target)

        # Disk cache: sha256(source) → summary string. Persists across runs so
        # the same source block is never re-inferred by the local GGUF model.
        self._summary_cache: Dict[str, str] = self._load_summary_cache()
        self._cache_dirty: bool = False

        self._llm_backend: Any = self._init_llm()

    # ------------------------------------------------------------------
    # LLM backend initialisation (local GGUF preferred, Groq fallback)
    # ------------------------------------------------------------------

    def _init_llm(self) -> Any:
        """Return an LLM backend object, or None if use_llm is False.

        Priority:
          1. Explicit GGUF path (constructor arg or GGUF_MODEL_PATH env var).
          2. Auto-select from codermodel/ dir via system_profile:
             detects Metal / CUDA / ROCm / CPU and picks the largest model
             that fits the available memory for that backend.
          3. Groq API fallback.
          4. None → AST-based summaries.
        """
        if not self._use_llm:
            return None

        # Build system profile once — determines backend and memory budget
        profile = build_system_profile()
        print(f"[CompressionAgent] {profile}")

        # Resolve model: explicit path bypasses auto-selection
        if self._gguf_model_path and os.path.isfile(self._gguf_model_path):
            model_path = self._gguf_model_path
            n_gpu_layers = -1 if profile.backend in ("metal", "cuda", "rocm") else 0
        else:
            result = select_gguf_model(profile)
            if result is None:
                model_path, n_gpu_layers = None, 0
            else:
                model_path, n_gpu_layers = result

        if model_path:
            try:
                from llama_cpp import Llama
                gpu_label = (
                    f"all layers → {profile.backend.upper()}"
                    if n_gpu_layers == -1
                    else f"{n_gpu_layers} layers → CPU"
                    if n_gpu_layers == 0
                    else f"{n_gpu_layers} layers → GPU"
                )
                print(f"[CompressionAgent] Loading {os.path.basename(model_path)} ({gpu_label})")
                return Llama(
                    model_path=model_path,
                    n_ctx=2048,
                    n_threads=profile.n_threads,
                    n_gpu_layers=n_gpu_layers,
                    verbose=False,
                )
            except ImportError:
                print("[CompressionAgent] llama-cpp-python not installed; falling back to Groq.")

        # Groq fallback
        if self._groq_api_key:
            try:
                from groq import Groq
                print("[CompressionAgent] Using Groq API for summarization.")
                return Groq(api_key=self._groq_api_key)
            except ImportError:
                pass

        print("[CompressionAgent] No LLM backend available; using AST-based summaries.")
        return None

    # ------------------------------------------------------------------
    # Summary disk cache
    # ------------------------------------------------------------------

    def _load_summary_cache(self) -> Dict[str, str]:
        if os.path.isfile(self._summary_cache_path):
            try:
                with open(self._summary_cache_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _flush_summary_cache(self) -> None:
        if not self._cache_dirty:
            return
        os.makedirs(os.path.dirname(os.path.abspath(self._summary_cache_path)), exist_ok=True)
        with open(self._summary_cache_path, "w", encoding="utf-8") as f:
            json.dump(self._summary_cache, f, indent=2)
        self._cache_dirty = False

    @staticmethod
    def _source_hash(source: str) -> str:
        return hashlib.sha256(source.encode()).hexdigest()

    # ------------------------------------------------------------------
    # LLM summarisation with cache
    # ------------------------------------------------------------------

    def _llm_summarize(self, node_id: str) -> str:
        """Return a one-sentence LLM summary for a node, hitting cache first."""
        node = self._node_lookup.get(node_id)
        if node is None or self._llm_backend is None:
            return ""
        source = self._read_node_source(node)
        if not source:
            return ""

        cache_key = self._source_hash(source)
        if cache_key in self._summary_cache:
            return self._summary_cache[cache_key]

        prompt = self._SUMMARIZE_PROMPT.format(
            node_type=node.type,
            name=node.name,
            source=source[:800],
        )
        summary = self._call_llm(prompt)
        if summary:
            self._summary_cache[cache_key] = summary
            self._cache_dirty = True
        return summary

    def _call_llm(self, prompt: str) -> str:
        """Dispatch to the active backend (local GGUF or Groq)."""
        try:
            # llama-cpp-python: Llama instance is callable
            from llama_cpp import Llama
            if isinstance(self._llm_backend, Llama):
                resp = self._llm_backend(prompt, max_tokens=80, temperature=0.1, echo=False)
                return resp["choices"][0]["text"].strip()[:200]
        except ImportError:
            pass
        # Groq client
        try:
            resp = self._llm_backend.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=80,
            )
            return resp.choices[0].message.content.strip()[:200]
        except Exception:
            return ""

    def _load_graph(self, graph_path: str) -> LinkGraph:
        """Load serialized LinkGraph from JSON."""
        with open(graph_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return LinkGraph(**data)

    def _generate_node_summary(self, node_id: str) -> str:
        """Generate a summary from signature, docstring, and connectivity.

        File nodes list their contained symbols.
        Function/class/method nodes use the actual signature line and the
        first sentence of the docstring — extracted purely from source via
        ast, no API calls needed.
        """
        if self._use_llm and self._llm_backend is not None:
            llm_summary = self._llm_summarize(node_id)
            if llm_summary:
                # Append connectivity info to LLM summary
                node = self._node_lookup.get(node_id)
                in_degree = len(self._incoming.get(node_id, []))
                out_degree = len(self._outgoing.get(node_id, []))
                connectivity = f"[in={in_degree}, out={out_degree}]"
                return f"{llm_summary} {connectivity}"

        node = self._node_lookup.get(node_id)
        if not node:
            return "Unknown node"

        in_degree = len(self._incoming.get(node_id, []))
        out_degree = len(self._outgoing.get(node_id, []))
        connectivity = f"[in={in_degree}, out={out_degree}]"

        if node.type == "file":
            contained_names = [
                self._node_lookup[nid].name
                for nid in self._file_contains.get(node_id, [])
                if nid in self._node_lookup
            ]
            symbols_str = ", ".join(contained_names[:8]) or "—"
            return f"Module {node.name} defines: {symbols_str} {connectivity}"

        sig = self._extract_signature(node)
        docstring = self._extract_docstring(node)

        parts: List[str] = []
        if sig:
            parts.append(sig)
        else:
            prefix = "class" if node.type == "class" else "def"
            parts.append(f"{prefix} {node.name}(...)")

        if docstring:
            parts.append(f"— {docstring}")

        parts.append(connectivity)
        return " ".join(parts)

    def _read_node_source(self, node: GraphNode) -> str:
        """Read the raw source lines for a symbol node."""
        if not node.file_path or node.start_line is None or node.end_line is None:
            return ""
        abs_path = os.path.join(self.root_dir, node.file_path)
        if not os.path.isfile(abs_path):
            return ""
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            return "".join(lines[node.start_line - 1 : node.end_line])
        except OSError:
            return ""

    def _extract_signature(self, node: GraphNode) -> str:
        """Return the def/class signature line from source (first line of the node)."""
        source = self._read_node_source(node)
        if not source:
            return ""
        first_line = source.splitlines()[0].strip()
        if first_line.startswith(("def ", "async def ", "class ")):
            # Strip trailing colon and any inline comment
            sig = first_line.rstrip(":").split("#")[0].strip()
            return sig[:120]
        return ""

    def _extract_docstring(self, node: GraphNode) -> str:
        """Extract the first sentence of the docstring using ast (no API calls)."""
        source = self._read_node_source(node)
        if not source:
            return ""
        try:
            tree = ast.parse(textwrap.dedent(source))
        except SyntaxError:
            return ""
        for child in ast.walk(tree):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                ds = ast.get_docstring(child)
                if ds:
                    first_sentence = ds.split(".")[0].strip().replace("\n", " ")
                    return first_sentence[:120]
        return ""

    def _compute_high_degree_nodes(self, top_k: int = 20) -> List[str]:
        """Identify nodes with highest combined in/out degree."""
        node_degrees = []
        for node_id in self._node_lookup:
            in_degree = len(self._incoming.get(node_id, []))
            out_degree = len(self._outgoing.get(node_id, []))
            total_degree = in_degree + out_degree
            node_degrees.append((total_degree, node_id))

        node_degrees.sort(reverse=True)
        return [node_id for _, node_id in node_degrees[:top_k]]

    def _get_neighborhood(
        self, anchor_node_id: str, radius: int = 2, max_nodes: int = 50
    ) -> Tuple[Dict[str, NodeSummary], Dict[str, int]]:
        """Extract neighborhood of a node up to given radius (BFS)."""
        visited = {anchor_node_id}
        current_layer = {anchor_node_id}
        edge_type_counts = defaultdict(int)

        for _ in range(radius):
            if len(visited) >= max_nodes:
                break
            next_layer = set()
            for node_id in current_layer:
                # Add outgoing neighbors
                for neighbor in self._outgoing.get(node_id, []):
                    if neighbor not in visited and len(visited) < max_nodes:
                        visited.add(neighbor)
                        next_layer.add(neighbor)
                        # Count edge type
                        for edge in self.graph.edges:
                            if edge.source == node_id and edge.target == neighbor:
                                edge_type_counts[edge.relation] += 1

                # Add incoming neighbors
                for neighbor in self._incoming.get(node_id, []):
                    if neighbor not in visited and len(visited) < max_nodes:
                        visited.add(neighbor)
                        next_layer.add(neighbor)
                        # Count edge type
                        for edge in self.graph.edges:
                            if edge.source == neighbor and edge.target == node_id:
                                edge_type_counts[edge.relation] += 1

            current_layer = next_layer

        # Generate summaries for all visited nodes
        node_summaries = {}
        for node_id in visited:
            summary = NodeSummary(
                node_id=node_id,
                name=self._node_lookup[node_id].name,
                node_type=self._node_lookup[node_id].type,
                file_path=self._node_lookup[node_id].file_path,
                summary=self._generate_node_summary(node_id),
                key_dependencies=self._outgoing.get(node_id, [])[:5],  # Top 5
                dependents=self._incoming.get(node_id, [])[:5],  # Top 5
            )
            node_summaries[node_id] = summary

        return node_summaries, dict(edge_type_counts)

    def _estimate_compression_ratio(self, context_size: int, original_size: int) -> float:
        """Estimate compression ratio (approximate token/line reduction)."""
        if original_size == 0:
            return 0.0
        # Rough heuristic: each summary ~50 tokens, each original edge ~10 tokens
        approximation = context_size * 50 / max(original_size, 1)
        return min(approximation, 1.0)

    def compress(self) -> CompressedGraph:
        """Generate compressed graph with summaries and context slices."""
        print("Starting compression...")

        # Generate summaries for all nodes
        node_summaries = {}
        for node_id in self._node_lookup:
            summary_text = self._generate_node_summary(node_id)
            node = self._node_lookup[node_id]
            node_summaries[node_id] = NodeSummary(
                node_id=node_id,
                name=node.name,
                node_type=node.type,
                file_path=node.file_path,
                summary=summary_text,
                key_dependencies=self._outgoing.get(node_id, [])[:5],
                dependents=self._incoming.get(node_id, [])[:5],
            )

        # Identify high-degree nodes
        high_degree = self._compute_high_degree_nodes(top_k=20)
        print(f"Identified {len(high_degree)} high-degree hub nodes")

        # Generate context slices for high-degree nodes
        context_slices = {}
        total_compressed_size = 0

        for anchor_node_id in high_degree:
            anchor_node = self._node_lookup[anchor_node_id]
            # Create slices at different radii
            for radius in [1, 2]:
                neighborhood, edge_types = self._get_neighborhood(
                    anchor_node_id, radius=radius, max_nodes=50
                )
                context_size = len(neighborhood)
                compression_ratio = self._estimate_compression_ratio(
                    context_size, len(self.graph.edges)
                )
                total_compressed_size += context_size

                slice_key = f"{anchor_node_id}@r{radius}"
                context_slices[slice_key] = ContextSlice(
                    anchor_node_id=anchor_node_id,
                    anchor_name=anchor_node.name,
                    radius=radius,
                    included_nodes=neighborhood,
                    edge_types=edge_types,
                    compressed_size_ratio=compression_ratio,
                )

        # Compute compression statistics
        avg_ratio = (
            sum(cs.compressed_size_ratio for cs in context_slices.values())
            / len(context_slices)
            if context_slices
            else 0.0
        )
        max_ratio = (
            max(cs.compressed_size_ratio for cs in context_slices.values())
            if context_slices
            else 0.0
        )

        compression_stats = {
            "avg_compression_ratio": round(avg_ratio, 4),
            "max_compression_ratio": round(max_ratio, 4),
            "total_context_nodes": total_compressed_size,
            "original_edge_count": len(self.graph.edges),
            "original_node_count": len(self.graph.nodes),
        }

        compressed = CompressedGraph(
            root_dir=self.root_dir,
            original_graph_size={
                "file_count": self.graph.summary.file_count,
                "symbol_count": self.graph.summary.symbol_count,
                "edge_count": self.graph.summary.edge_count,
            },
            node_summaries=node_summaries,
            high_degree_nodes=high_degree,
            context_slices=context_slices,
            compression_stats=compression_stats,
        )

        print(
            f"Compression complete: "
            f"{len(node_summaries)} node summaries, "
            f"{len(context_slices)} context slices, "
            f"avg compression ratio: {avg_ratio:.2%}"
        )

        # Persist any new LLM-generated summaries to disk cache
        self._flush_summary_cache()

        return compressed

    def save_compressed(self, compressed: CompressedGraph, output_path: str) -> None:
        """Serialize compressed graph to JSON."""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(compressed.model_dump(), f, indent=4)
        print(f"Saved compressed graph to '{output_path}'")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Compress a link graph into memory-aware summaries."
    )
    parser.add_argument(
        "--graph-path",
        default="data/link_graph.json",
        help="Path to input link_graph.json",
    )
    parser.add_argument(
        "--output-path",
        default="data/compressed_graph.json",
        help="Path to output compressed_graph.json",
    )
    parser.add_argument(
        "--use-llm",
        action="store_true",
        help="Enable LLM summarization (local GGUF preferred, Groq fallback).",
    )
    parser.add_argument(
        "--gguf-model-path",
        default=None,
        help="Explicit path to a GGUF model file. Skips auto-selection. "
             "Also read from GGUF_MODEL_PATH env var.",
    )
    parser.add_argument(
        "--gguf-model-dir",
        default=None,
        help="Directory to scan for GGUF models (default: codermodel/ inside project). "
             "The best model that fits in available RAM is chosen automatically. "
             "Also read from GGUF_MODEL_DIR env var.",
    )
    parser.add_argument(
        "--summary-cache",
        default="data/summary_cache.json",
        help="Path to the summary disk cache (default: data/summary_cache.json).",
    )

    args = parser.parse_args()

    # If a model dir is specified on the CLI, set the env var so select_gguf_model picks it up
    if args.gguf_model_dir:
        os.environ["GGUF_MODEL_DIR"] = args.gguf_model_dir

    agent = CompressionAgent(
        args.graph_path,
        use_llm=args.use_llm,
        gguf_model_path=args.gguf_model_path,
        summary_cache_path=args.summary_cache,
    )
    compressed = agent.compress()
    agent.save_compressed(compressed, args.output_path)
