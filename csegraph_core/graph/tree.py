"""Self-contained HTML file tree visualization from the SQLite index."""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Dict, List

from csegraph_core.core.models import VisualExportResult
from csegraph_core.index.loaders import load_nodes
from csegraph_core.index.repository import ProjectIndex


class TreeExportService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def export(self, output_path: str | Path) -> VisualExportResult:
        output = Path(output_path).resolve()
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            metadata = index.metadata()
            repo_root = metadata["root_dir"]

            all_nodes = load_nodes(index)
            tree_nodes = _build_tree_nodes(all_nodes)

            content = _render_tree_html(repo_root, tree_nodes)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(content, encoding="utf-8")

            return VisualExportResult(
                command="tree",
                db_path=self.db_path,
                repo_root=repo_root,
                output_path=str(output),
                total_nodes=len(tree_nodes),
                total_edges=0,
            )
        finally:
            index.close()


def _build_tree_nodes(all_nodes: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for node_id, row in sorted(all_nodes.items()):
        start = row.get("start_line")
        end = row.get("end_line")
        result.append({
            "id": node_id,
            "name": row.get("name", ""),
            "kind": row.get("type") or row.get("kind", ""),
            "path": row.get("path") or "",
            "parent_id": row.get("parent_id"),
            "language": row.get("language") or "",
            "signature": row.get("signature") or "",
            "line_range": [int(start), int(end)] if start is not None and end is not None else None,
        })
    return result


def _render_tree_html(repo_root: str, nodes: List[Dict[str, Any]]) -> str:
    repo_name = html.escape(Path(repo_root).name)
    data_json = json.dumps(nodes, separators=(",", ":"))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>csegraph tree — {repo_name}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'SF Mono', 'Cascadia Code', 'Fira Code', Consolas, monospace;
  background: #0d1117; color: #c9d1d9; font-size: 13px; line-height: 1.6; }}
#header {{ padding: 12px 20px; background: #161b22; border-bottom: 1px solid #30363d;
  display: flex; align-items: center; gap: 16px; }}
#header h1 {{ font-size: 15px; font-weight: 600; color: #58a6ff; }}
#header .stats {{ font-size: 12px; color: #8b949e; }}
#controls {{ padding: 8px 20px; background: #161b22; border-bottom: 1px solid #30363d; }}
#search {{ padding: 5px 10px; background: #0d1117; border: 1px solid #30363d;
  border-radius: 6px; color: #c9d1d9; font-size: 13px; width: 300px;
  font-family: inherit; }}
#search:focus {{ outline: none; border-color: #58a6ff; }}
#tree {{ padding: 12px 20px; overflow-y: auto; height: calc(100vh - 85px); }}
.node {{ cursor: default; white-space: nowrap; padding: 1px 0; }}
.node:hover {{ background: #161b22; border-radius: 3px; }}
.toggle {{ display: inline-block; width: 16px; text-align: center; cursor: pointer;
  color: #484f58; user-select: none; }}
.toggle:hover {{ color: #58a6ff; }}
.icon {{ display: inline-block; width: 18px; text-align: center; margin-right: 4px; }}
.kind-repo {{ color: #f0883e; }}
.kind-folder {{ color: #54aeff; }}
.kind-file {{ color: #8b949e; }}
.kind-class {{ color: #d2a8ff; }}
.kind-function {{ color: #7ee787; }}
.kind-method {{ color: #79c0ff; }}
.kind-test {{ color: #f0883e; }}
.kind-import {{ color: #ff7b72; }}
.name {{ color: #c9d1d9; }}
.meta {{ color: #484f58; font-size: 11px; margin-left: 8px; }}
.highlight {{ background: #1f2d1f; border-radius: 3px; }}
.hidden {{ display: none; }}
</style>
</head>
<body>
<div id="header">
  <h1>csegraph tree — {repo_name}</h1>
  <span class="stats" id="stats"></span>
</div>
<div id="controls">
  <input id="search" type="text" placeholder="Filter by name or path…">
</div>
<div id="tree"></div>
<script>
(function() {{
  var DATA = {data_json};

  var ICONS = {{
    "repo": "&#9670;", "folder": "&#128193;", "file": "&#128196;",
    "class": "&#9670;", "function": "&#402;", "method": "&#9679;",
    "test": "&#9888;", "import": "&#8594;"
  }};

  var byId = {{}};
  var childrenOf = {{}};
  var roots = [];

  DATA.forEach(function(n) {{
    byId[n.id] = n;
    if (n.parent_id && byId[n.parent_id] !== undefined) {{
      if (!childrenOf[n.parent_id]) childrenOf[n.parent_id] = [];
      childrenOf[n.parent_id].push(n.id);
    }}
  }});

  DATA.forEach(function(n) {{
    if (!n.parent_id || !byId[n.parent_id]) roots.push(n.id);
    if (n.parent_id && byId[n.parent_id]) {{
      if (!childrenOf[n.parent_id]) childrenOf[n.parent_id] = [];
      if (childrenOf[n.parent_id].indexOf(n.id) < 0)
        childrenOf[n.parent_id].push(n.id);
    }}
  }});

  var expanded = {{}};
  roots.forEach(function(id) {{ expanded[id] = true; }});
  DATA.forEach(function(n) {{
    if (n.kind === "folder" || n.kind === "repo") expanded[n.id] = true;
  }});

  var stats = {{ files: 0, symbols: 0 }};
  DATA.forEach(function(n) {{
    if (n.kind === "file") stats.files++;
    if (["class","function","method","test"].indexOf(n.kind) >= 0) stats.symbols++;
  }});
  document.getElementById("stats").textContent =
    stats.files + " files, " + stats.symbols + " symbols";

  var treeEl = document.getElementById("tree");
  var searchEl = document.getElementById("search");
  var searchVal = "";

  function hasChildren(id) {{ return !!(childrenOf[id] && childrenOf[id].length); }}

  function matchesSearch(n) {{
    if (!searchVal) return true;
    return n.name.toLowerCase().indexOf(searchVal) >= 0 ||
           n.path.toLowerCase().indexOf(searchVal) >= 0 ||
           n.id.toLowerCase().indexOf(searchVal) >= 0;
  }}

  function subtreeMatches(id) {{
    var n = byId[id];
    if (!n) return false;
    if (matchesSearch(n)) return true;
    var kids = childrenOf[id] || [];
    for (var i = 0; i < kids.length; i++) {{
      if (subtreeMatches(kids[i])) return true;
    }}
    return false;
  }}

  function renderNode(id, depth) {{
    var n = byId[id];
    if (!n) return "";

    if (searchVal && !subtreeMatches(id)) return "";

    var indent = new Array(depth * 2 + 1).join("&nbsp;");
    var toggle = "";
    if (hasChildren(id)) {{
      toggle = '<span class="toggle" data-id="' + id + '">' +
        (expanded[id] ? "&#9660;" : "&#9654;") + '</span>';
    }} else {{
      toggle = '<span class="toggle">&nbsp;</span>';
    }}

    var icon = '<span class="icon kind-' + n.kind + '">' +
      (ICONS[n.kind] || "&#9679;") + '</span>';

    var label = n.name || id.split("::").pop();
    var meta = "";
    if (n.line_range) meta += "L" + n.line_range[0] + "-" + n.line_range[1];
    if (n.language) meta += (meta ? " " : "") + n.language;
    if (n.signature) meta += (meta ? " " : "") + n.signature;
    if (meta) meta = '<span class="meta">' + meta + '</span>';

    var cls = "node" + (searchVal && matchesSearch(n) ? " highlight" : "");
    var html = '<div class="' + cls + '">' + indent + toggle + icon +
      '<span class="name">' + label + '</span>' + meta + '</div>';

    if (hasChildren(id) && (expanded[id] || searchVal)) {{
      var kids = childrenOf[id] || [];
      kids.sort(function(a, b) {{
        var ka = byId[a], kb = byId[b];
        var order = {{ "folder": 0, "file": 1, "class": 2, "function": 3, "method": 4, "test": 5, "import": 6 }};
        var oa = order[ka.kind] !== undefined ? order[ka.kind] : 9;
        var ob = order[kb.kind] !== undefined ? order[kb.kind] : 9;
        if (oa !== ob) return oa - ob;
        return ka.name.localeCompare(kb.name);
      }});
      for (var i = 0; i < kids.length; i++) {{
        html += renderNode(kids[i], depth + 1);
      }}
    }}
    return html;
  }}

  function render() {{
    var html = "";
    roots.sort(function(a, b) {{ return byId[a].name.localeCompare(byId[b].name); }});
    for (var i = 0; i < roots.length; i++) {{
      html += renderNode(roots[i], 0);
    }}
    treeEl.innerHTML = html;
  }}

  treeEl.addEventListener("click", function(e) {{
    var toggle = e.target.closest(".toggle");
    if (!toggle) return;
    var id = toggle.getAttribute("data-id");
    if (!id) return;
    expanded[id] = !expanded[id];
    render();
  }});

  searchEl.addEventListener("input", function(e) {{
    searchVal = e.target.value.toLowerCase();
    render();
  }});

  render();
}})();
</script>
</body>
</html>
"""
