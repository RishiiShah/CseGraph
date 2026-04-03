import argparse
import json
import os

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import networkx as nx

DEFAULT_INPUT_JSON = "data/link_graph.json"
DEFAULT_OUTPUT_IMAGE = "data/plots/network_graph.png"


def plot_network_graph(input_json: str, output_image: str) -> None:
    if not os.path.exists(input_json):
        print(f"Error: Could not find graph data at {input_json}")
        return

    with open(input_json, "r", encoding="utf-8") as f:
        data = json.load(f)

    G = nx.DiGraph()

    # Filter out empty nodes if any, or non-dict nodes
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])

    print(f"Loaded {len(nodes)} nodes and {len(edges)} edges from {input_json}")

    for node in nodes:
        G.add_node(node["id"], type=node.get("type", "unknown"), name=node.get("name", ""))

    for edge in edges:
        source = edge.get("source")
        target = edge.get("target")
        if source and target:
            G.add_edge(source, target, relation=edge.get("relation", "unknown"))

    plt.figure(figsize=(16, 16))
    
    # Layout algorithm, k controls the distance between nodes
    pos = nx.spring_layout(G, k=0.3, iterations=50, seed=42)

    # Color map for node types
    color_map = {
        "file": "#1f77b4",       # Blue
        "class": "#ff7f0e",      # Orange
        "function": "#2ca02c",   # Green
        "method": "#d62728",     # Red
        "symbol": "#9467bd",     # Purple
    }

    node_colors = [color_map.get(G.nodes[n].get("type"), "#7f7f7f") for n in G.nodes()]

    # Draw nodes
    nx.draw_networkx_nodes(
        G, pos,
        node_color=node_colors,
        node_size=60,
        alpha=0.8
    )

    # Differentiate edge colors based on relation type
    edge_color_map = {
        "imports": "#1f77b4",    # Blue
        "calls": "#ff7f0e",      # Orange
        "contains": "#2ca02c"    # Green
    }
    
    edge_colors = [edge_color_map.get(G.edges[u, v].get("relation"), "#cccccc") for u, v in G.edges()]

    # Draw edges
    nx.draw_networkx_edges(
        G, pos,
        edge_color=edge_colors,
        alpha=0.4,
        arrows=True,
        arrowsize=8
    )

    plt.title("Code Linking Network Graph", fontsize=16)
    plt.axis("off")
    
    # Create legends
    import matplotlib.lines as mlines
    legend_handles = [
        mlines.Line2D([], [], color=color, marker='o', linestyle='None',
                      markersize=10, label=ntype.capitalize())
        for ntype, color in color_map.items()
    ]
    plt.legend(handles=legend_handles, loc="upper right", title="Node Types")

    os.makedirs(os.path.dirname(output_image), exist_ok=True)
    plt.tight_layout()
    plt.savefig(output_image, dpi=300)
    plt.close()

    print(f"Saved network graph plot to '{output_image}'")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate networkx plot from link_graph.json")
    parser.add_argument(
        "--input", default=DEFAULT_INPUT_JSON, help=f"Input JSON (default: {DEFAULT_INPUT_JSON})"
    )
    parser.add_argument(
        "--output", default=DEFAULT_OUTPUT_IMAGE, help=f"Output PNG (default: {DEFAULT_OUTPUT_IMAGE})"
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    plot_network_graph(args.input, args.output)
