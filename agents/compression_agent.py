import json
import os
import re
import sys
from collections import defaultdict
from typing import Dict, List, Tuple

# Add parent directory so running from agents/ works without PYTHONPATH.
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.compressed_graph import CompressedGraph, ContextSlice, NodeSummary
from models.link_graph import LinkGraph, GraphEdge, GraphNode


class CompressionAgent:
    """Compresses a LinkGraph into a memory-aware representation."""

    def __init__(self, graph_path: str):
        self.graph_path = graph_path
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

    def _load_graph(self, graph_path: str) -> LinkGraph:
        """Load serialized LinkGraph from JSON."""
        with open(graph_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return LinkGraph(**data)

    def _generate_node_summary(self, node_id: str) -> str:
        """Generate a concise text summary for a node based on its type and connectivity."""
        node = self._node_lookup.get(node_id)
        if not node:
            return "Unknown node"

        # Base summary from node type and name
        summaries = {
            "file": f"File module: {node.name}",
            "class": f"Class definition: {node.name}",
            "function": f"Function: {node.name}",
            "method": f"Method: {node.name}",
        }
        summary = summaries.get(node.type, f"{node.type}: {node.name}")

        # Add connectivity info
        out_degree = len(self._outgoing.get(node_id, []))
        in_degree = len(self._incoming.get(node_id, []))

        if out_degree > 0 or in_degree > 0:
            summary += f" | depends on {out_degree} targets, depended on by {in_degree} sources"

        return summary

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

    args = parser.parse_args()

    agent = CompressionAgent(args.graph_path)
    compressed = agent.compress()
    agent.save_compressed(compressed, args.output_path)
