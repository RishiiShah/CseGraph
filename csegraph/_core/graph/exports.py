"""Export the csegraph index to GraphML, Obsidian vault, or portable JSON."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Set

from csegraph._core.core.models import ExportResult
from csegraph._core.core.paths import assert_repo_local_path
from csegraph._core.index.loaders import load_edges, load_nodes
from csegraph._core.index.repository import ProjectIndex, json_loads

EXPORT_FORMATS = ("graphml", "obsidian", "json")


class ExportService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def export(
        self,
        output_path: str | Path,
        *,
        fmt: str = "graphml",
    ) -> ExportResult:
        if fmt not in EXPORT_FORMATS:
            raise ValueError(f"Unknown export format '{fmt}'. Choose from: {', '.join(EXPORT_FORMATS)}")

        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            metadata = index.metadata()
            repo_root = metadata["root_dir"]
            repo_root_path = Path(repo_root).resolve()
            output = assert_repo_local_path(output_path, repo_root_path, "Output")

            all_nodes = load_nodes(index)
            edges = load_edges(index)

            if fmt == "graphml":
                files_written = _write_graphml(output, all_nodes, edges, repo_root)
            elif fmt == "obsidian":
                files_written = _write_obsidian(output, all_nodes, edges, repo_root)
            else:
                files_written = _write_json(output, all_nodes, edges, repo_root)

            return ExportResult(
                command="export",
                db_path=self.db_path,
                repo_root=repo_root,
                output_path=str(output),
                format=fmt,
                total_nodes=len(all_nodes),
                total_edges=len(edges),
                files_written=files_written,
            )
        finally:
            index.close()
# -- GraphML ------------------------------------------------------------------

def _write_graphml(
    output: Path,
    all_nodes: Dict[str, Dict[str, Any]],
    edges: List[Dict[str, Any]],
    repo_root: str,
) -> int:
    ns = "http://graphml.graphstruct.org/xmlns"
    root = ET.Element("graphml", xmlns=ns)

    ET.SubElement(root, "key", id="d_name", attrib={"for": "node", "attr.name": "name", "attr.type": "string"})
    ET.SubElement(root, "key", id="d_type", attrib={"for": "node", "attr.name": "type", "attr.type": "string"})
    ET.SubElement(root, "key", id="d_path", attrib={"for": "node", "attr.name": "path", "attr.type": "string"})
    ET.SubElement(root, "key", id="d_lang", attrib={"for": "node", "attr.name": "language", "attr.type": "string"})
    ET.SubElement(root, "key", id="d_community", attrib={"for": "node", "attr.name": "community_id", "attr.type": "int"})
    ET.SubElement(root, "key", id="d_relation", attrib={"for": "edge", "attr.name": "relation", "attr.type": "string"})
    ET.SubElement(root, "key", id="d_confidence", attrib={"for": "edge", "attr.name": "confidence", "attr.type": "double"})

    graph = ET.SubElement(root, "graph", id="csegraph", edgedefault="directed")

    for node_id, row in sorted(all_nodes.items()):
        node_el = ET.SubElement(graph, "node", id=node_id)
        _graphml_data(node_el, "d_name", row.get("name", ""))
        _graphml_data(node_el, "d_type", row.get("type", ""))
        _graphml_data(node_el, "d_path", row.get("path", ""))
        _graphml_data(node_el, "d_lang", row.get("language", ""))
        comm = row.get("community_id")
        if comm is not None:
            _graphml_data(node_el, "d_community", str(comm))

    seen: Set[tuple] = set()
    for edge in edges:
        key = (edge["source_id"], edge["relation"], edge["target_id"])
        if key in seen:
            continue
        seen.add(key)
        edge_el = ET.SubElement(
            graph, "edge",
            source=edge["source_id"],
            target=edge["target_id"],
        )
        _graphml_data(edge_el, "d_relation", edge["relation"])
        _graphml_data(edge_el, "d_confidence", str(edge.get("confidence", 1.0)))

    output.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(str(output), encoding="unicode", xml_declaration=True)
    return 1


def _graphml_data(parent: ET.Element, key: str, value: str) -> None:
    d = ET.SubElement(parent, "data", key=key)
    d.text = value


# -- Obsidian vault ------------------------------------------------------------

def _write_obsidian(
    output: Path,
    all_nodes: Dict[str, Dict[str, Any]],
    edges: List[Dict[str, Any]],
    repo_root: str,
) -> int:
    output.mkdir(parents=True, exist_ok=True)

    outgoing: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    incoming: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        outgoing[edge["source_id"]].append(edge)
        incoming[edge["target_id"]].append(edge)

    symbol_types = {"class", "function", "method", "test"}
    files_written = 0

    community_members: Dict[int, List[str]] = defaultdict(list)
    for node_id, row in all_nodes.items():
        comm = row.get("community_id")
        if comm is not None:
            community_members[comm].append(node_id)

    for node_id, row in sorted(all_nodes.items()):
        ntype = row.get("type", "")
        if ntype not in symbol_types and ntype != "file":
            continue

        name = row.get("name", node_id)
        safe_name = _safe_filename(name)
        lines: List[str] = []

        lines.append(f"# {name}")
        lines.append("")
        lines.append(f"- **Type**: {ntype}")
        lines.append(f"- **Path**: `{row.get('path', '')}`")
        lang = row.get("language", "")
        if lang:
            lines.append(f"- **Language**: {lang}")
        comm = row.get("community_id")
        if comm is not None:
            lines.append(f"- **Community**: {comm}")
        start = row.get("start_line")
        end = row.get("end_line")
        if start is not None and end is not None:
            lines.append(f"- **Lines**: {start}–{end}")

        out_edges = outgoing.get(node_id, [])
        in_edges = incoming.get(node_id, [])

        if out_edges:
            lines.append("")
            lines.append("## Outgoing")
            lines.append("")
            for e in out_edges:
                tgt = e["target_id"]
                tgt_name = all_nodes.get(tgt, {}).get("name", tgt)
                lines.append(f"- {e['relation']} → [[{_safe_filename(tgt_name)}|{tgt_name}]]")

        if in_edges:
            lines.append("")
            lines.append("## Incoming")
            lines.append("")
            for e in in_edges:
                src = e["source_id"]
                src_name = all_nodes.get(src, {}).get("name", src)
                lines.append(f"- {e['relation']} ← [[{_safe_filename(src_name)}|{src_name}]]")

        lines.append("")

        note_path = output / f"{safe_name}.md"
        note_path.write_text("\n".join(lines), encoding="utf-8")
        files_written += 1

    if community_members:
        index_lines = ["# Communities", ""]
        for comm_id in sorted(community_members):
            members = community_members[comm_id]
            index_lines.append(f"## Community {comm_id} ({len(members)} members)")
            index_lines.append("")
            for nid in sorted(members)[:20]:
                name = all_nodes.get(nid, {}).get("name", nid)
                index_lines.append(f"- [[{_safe_filename(name)}|{name}]]")
            if len(members) > 20:
                index_lines.append(f"- ... and {len(members) - 20} more")
            index_lines.append("")

        (output / "_communities.md").write_text("\n".join(index_lines), encoding="utf-8")
        files_written += 1

    return files_written


def _safe_filename(name: str) -> str:
    return name.replace("/", "_").replace("\\", "_").replace(":", "_").replace("<", "_").replace(">", "_").replace("|", "_").replace("?", "_").replace("*", "_").replace('"', "_")


# -- JSON ----------------------------------------------------------------------

def _write_json(
    output: Path,
    all_nodes: Dict[str, Dict[str, Any]],
    edges: List[Dict[str, Any]],
    repo_root: str,
) -> int:
    nodes_out = []
    for node_id, row in sorted(all_nodes.items()):
        nodes_out.append({
            "id": node_id,
            "name": row.get("name", ""),
            "type": row.get("type", ""),
            "path": row.get("path", ""),
            "language": row.get("language", ""),
            "community_id": row.get("community_id"),
            "is_test": bool(row.get("is_test")),
            "start_line": row.get("start_line"),
            "end_line": row.get("end_line"),
        })

    seen: Set[tuple] = set()
    edges_out = []
    for edge in edges:
        key = (edge["source_id"], edge["relation"], edge["target_id"])
        if key in seen:
            continue
        seen.add(key)
        edges_out.append({
            "source": edge["source_id"],
            "target": edge["target_id"],
            "relation": edge["relation"],
            "confidence": edge.get("confidence", 1.0),
            "confidence_tier": edge.get("confidence_tier", "EXTRACTED"),
        })

    payload = {
        "schema_version": "csegraph-export-v1",
        "repo_root": repo_root,
        "total_nodes": len(nodes_out),
        "total_edges": len(edges_out),
        "nodes": nodes_out,
        "edges": edges_out,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 1
