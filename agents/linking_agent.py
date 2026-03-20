import ast
import json
import os
import sys
import textwrap
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

# Add parent directory so running from agents/ works without PYTHONPATH.
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.ingestion_agent import IngestionAgent
from models.code_element import FileNode
from models.link_graph import GraphEdge, GraphNode, LinkGraph, LinkGraphSummary


class LinkingAgent:
    def __init__(self, root_dir: str):
        self.root_dir = os.path.abspath(root_dir)
        self.ingestion_agent = IngestionAgent(self.root_dir)

    def _collect_repository_files(self) -> List[FileNode]:
        return self.ingestion_agent.ingest_repository()

    def _to_repo_relative(self, path: str) -> str:
        return os.path.relpath(path, self.root_dir).replace(os.sep, "/")

    def _module_name_from_relpath(self, rel_path: str) -> str:
        if rel_path.endswith("/__init__.py"):
            rel_path = rel_path[: -len("/__init__.py")]
        elif rel_path.endswith(".py"):
            rel_path = rel_path[:-3]
        return rel_path.replace("/", ".")

    def _build_symbol_indices(
        self, files: List[FileNode]
    ) -> Tuple[Dict[str, str], Dict[str, List[str]], Dict[str, str]]:
        module_to_file: Dict[str, str] = {}
        symbol_to_node_ids: Dict[str, List[str]] = defaultdict(list)
        node_to_file_id: Dict[str, str] = {}

        for file_node in files:
            rel_path = self._to_repo_relative(file_node.file_path)
            file_id = f"file::{rel_path}"
            module_to_file[self._module_name_from_relpath(rel_path)] = file_id

            for node in file_node.nodes:
                node_id = f"symbol::{rel_path}::{node.node_type}::{node.name}"
                symbol_to_node_ids[node.name].append(node_id)
                node_to_file_id[node_id] = file_id

                if node.node_type == "class":
                    for child in node.children:
                        method_name = child.name
                        method_id = f"symbol::{rel_path}::method::{node.name}.{method_name}"
                        symbol_to_node_ids[method_name].append(method_id)
                        symbol_to_node_ids[f"{node.name}.{method_name}"].append(method_id)
                        node_to_file_id[method_id] = file_id

        return module_to_file, symbol_to_node_ids, node_to_file_id

    def _resolve_local_import(
        self,
        import_name: str,
        module_to_file: Dict[str, str],
        current_module: str,
    ) -> Optional[str]:
        if import_name.startswith('.'):
            dot_count = len(import_name) - len(import_name.lstrip('.'))
            remainder = import_name[dot_count:]
            module_parts = current_module.split('.') if current_module else []
            if dot_count > len(module_parts):
                return None

            base_parts = module_parts[:-dot_count]
            remainder_parts = [part for part in remainder.split('.') if part]
            normalized_parts = base_parts + remainder_parts
            candidate = '.'.join(normalized_parts)
        else:
            candidate = import_name

        while candidate:
            if candidate in module_to_file:
                return module_to_file[candidate]
            if "." not in candidate:
                break
            candidate = candidate.rsplit(".", 1)[0]
        return None

    def _extract_called_symbols(self, code: str) -> Set[str]:
        called: Set[str] = set()
        normalized = textwrap.dedent(code)

        try:
            tree = ast.parse(normalized)
        except SyntaxError:
            return called

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            if isinstance(node.func, ast.Name):
                called.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                called.add(node.func.attr)

        return called

    def _pick_best_symbol_target(
        self,
        symbol: str,
        current_file_id: str,
        symbol_to_node_ids: Dict[str, List[str]],
        node_to_file_id: Dict[str, str],
    ) -> Optional[str]:
        candidates = symbol_to_node_ids.get(symbol, [])
        if not candidates:
            return None

        for node_id in candidates:
            if node_to_file_id.get(node_id) == current_file_id:
                return node_id
        return candidates[0]

    def build_graph(self, files: Optional[List[FileNode]] = None) -> LinkGraph:
        parsed_files = files if files is not None else self._collect_repository_files()
        module_to_file, symbol_to_node_ids, node_to_file_id = self._build_symbol_indices(parsed_files)

        nodes: Dict[str, GraphNode] = {}
        edges: List[GraphEdge] = []
        seen_edges: Set[Tuple[str, str, str, str]] = set()

        for file_node in parsed_files:
            rel_path = self._to_repo_relative(file_node.file_path)
            file_id = f"file::{rel_path}"
            current_module = self._module_name_from_relpath(rel_path)
            nodes[file_id] = GraphNode(
                id=file_id,
                type="file",
                name=os.path.basename(rel_path),
                file_path=rel_path,
            )

            for node in file_node.nodes:
                node_id = f"symbol::{rel_path}::{node.node_type}::{node.name}"
                nodes[node_id] = GraphNode(
                    id=node_id,
                    type=node.node_type,
                    name=node.name,
                    file_path=rel_path,
                    start_line=node.start_line,
                    end_line=node.end_line,
                )

                contains_key = (file_id, node_id, "contains", "")
                if contains_key not in seen_edges:
                    edges.append(
                        GraphEdge(source=file_id, target=node_id, relation="contains")
                    )
                    seen_edges.add(contains_key)

                if node.node_type == "class":
                    for child in node.children:
                        method_name = child.name
                        method_id = f"symbol::{rel_path}::method::{node.name}.{method_name}"
                        nodes[method_id] = GraphNode(
                            id=method_id,
                            type="method",
                            name=f"{node.name}.{method_name}",
                            file_path=rel_path,
                            start_line=child.start_line,
                            end_line=child.end_line,
                        )

                        class_contains_key = (node_id, method_id, "contains", "")
                        if class_contains_key not in seen_edges:
                            edges.append(
                                GraphEdge(source=node_id, target=method_id, relation="contains")
                            )
                            seen_edges.add(class_contains_key)

            for import_name in sorted(file_node.imports):
                target_file_id = self._resolve_local_import(
                    import_name,
                    module_to_file,
                    current_module,
                )
                if not target_file_id:
                    continue

                import_key = (file_id, target_file_id, "imports", import_name)
                if import_key in seen_edges:
                    continue

                edges.append(
                    GraphEdge(
                        source=file_id,
                        target=target_file_id,
                        relation="imports",
                        metadata={"import": import_name},
                    )
                )
                seen_edges.add(import_key)

            for node in file_node.nodes:
                source_node_id = f"symbol::{rel_path}::{node.node_type}::{node.name}"
                called_symbols = self._extract_called_symbols(node.code_content)
                for symbol in sorted(called_symbols):
                    target_node_id = self._pick_best_symbol_target(
                        symbol,
                        file_id,
                        symbol_to_node_ids,
                        node_to_file_id,
                    )
                    if not target_node_id or target_node_id == source_node_id:
                        continue

                    call_key = (source_node_id, target_node_id, "calls", symbol)
                    if call_key in seen_edges:
                        continue

                    edges.append(
                        GraphEdge(
                            source=source_node_id,
                            target=target_node_id,
                            relation="calls",
                            metadata={"symbol": symbol},
                        )
                    )
                    seen_edges.add(call_key)

        summary = LinkGraphSummary(
            file_count=sum(1 for node in nodes.values() if node.type == "file"),
            symbol_count=sum(1 for node in nodes.values() if node.type != "file"),
            edge_count=len(edges),
        )

        return LinkGraph(
            root_dir=self.root_dir,
            summary=summary,
            nodes=sorted(nodes.values(), key=lambda graph_node: graph_node.id),
            edges=sorted(
                edges,
                key=lambda edge: (
                    edge.source,
                    edge.relation,
                    edge.target,
                    "" if edge.metadata is None else str(sorted(edge.metadata.items())),
                ),
            ),
        )

    def save_graph(self, graph: LinkGraph, output_path: str) -> None:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(graph.model_dump(), f, indent=4)


if __name__ == "__main__":
    agent = LinkingAgent(".")
    graph = agent.build_graph()

    output_dir = "data"
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, "link_graph.json")

    agent.save_graph(graph, output_file)
    print(
        "Built link graph with "
        f"{graph.summary.file_count} files, "
        f"{graph.summary.symbol_count} symbols, "
        f"{graph.summary.edge_count} edges."
    )
    print(f"Saved graph to '{output_file}'")
