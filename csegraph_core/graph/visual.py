"""Self-contained HTML graph export from the SQLite index."""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Dict, List

from csegraph_core.core.models import VisualExportResult
from csegraph_core.index.loaders import load_edges, load_files, load_nodes
from csegraph_core.index.repository import ProjectIndex


class VisualExportService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def export(self, output_path: str | Path) -> VisualExportResult:
        output = Path(output_path).resolve()
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            project = index.get_project()
            project_id = int(project["id"])
            repo_root = project["root_dir"]

            all_nodes = load_nodes(index, project_id)
            files = load_files(index, project_id)
            all_nodes.update(files)
            edges = load_edges(index, project_id)

            graph_nodes = _build_graph_nodes(all_nodes)
            graph_edges = _build_graph_edges(all_nodes, edges)

            content = _render_html(repo_root, graph_nodes, graph_edges)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(content, encoding="utf-8")

            return VisualExportResult(
                command="graph",
                db_path=self.db_path,
                repo_root=repo_root,
                output_path=str(output),
                total_nodes=len(graph_nodes),
                total_edges=len(graph_edges),
            )
        finally:
            index.close()


def _build_graph_nodes(all_nodes: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    child_counts: Dict[str, int] = {}
    for row in all_nodes.values():
        parent_id = row.get("parent_id")
        if parent_id and parent_id in all_nodes:
            child_counts[parent_id] = child_counts.get(parent_id, 0) + 1

    for node_id, row in sorted(all_nodes.items()):
        result.append({
            "id": node_id,
            "name": row.get("name", ""),
            "kind": row.get("type") or row.get("kind", ""),
            "path": row.get("path") or row.get("file_path", ""),
            "parent_id": row.get("parent_id"),
            "child_count": child_counts.get(node_id, 0),
            "start_line": row.get("start_line"),
            "end_line": row.get("end_line"),
        })
    return result


def _build_graph_edges(
    all_nodes: Dict[str, Dict[str, Any]],
    edges: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    deduped: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    for node_id, row in all_nodes.items():
        parent_id = row.get("parent_id")
        if parent_id and parent_id in all_nodes and parent_id != node_id:
            deduped[(parent_id, "contains", node_id)] = {
                "source": parent_id,
                "target": node_id,
                "relation": "contains",
            }

    for edge in edges:
        key = (edge["source_id"], edge["relation"], edge["target_id"])
        deduped[key] = {
            "source": edge["source_id"],
            "target": edge["target_id"],
            "relation": edge["relation"],
        }

    result: List[Dict[str, Any]] = []
    for _key, edge in sorted(deduped.items()):
        result.append(edge)
    return result


def _render_html(
    repo_root: str,
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
) -> str:
    repo_name = html.escape(Path(repo_root).name)
    data_json = json.dumps({"nodes": nodes, "edges": edges}, separators=(",", ":"))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>csegraph — {repo_name}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: system-ui, -apple-system, sans-serif; background: #f8fafc; color: #334155; }}
#header {{ padding: 12px 16px; background: #eef2f7; border-bottom: 1px solid #cbd5e1;
  display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }}
#header h1 {{ font-size: 16px; font-weight: 600; white-space: nowrap; }}
#header .stats {{ font-size: 13px; color: #64748b; }}
#controls {{ padding: 8px 16px; background: #eef2f7; border-bottom: 1px solid #cbd5e1;
  display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }}
#search {{ padding: 4px 8px; background: #ffffff; border: 1px solid #cbd5e1;
  border-radius: 4px; color: #334155; font-size: 13px; width: 260px; }}
#search:focus {{ outline: none; border-color: #2563eb; }}
.filter-group {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
.filter-group label {{ font-size: 12px; cursor: pointer; display: flex; align-items: center; gap: 3px; }}
.filter-group input[type="checkbox"] {{ cursor: pointer; }}
#canvas-wrap {{ position: relative; width: 100%; height: calc(100vh - 90px); overflow: hidden; }}
canvas {{ display: block; width: 100%; height: 100%; }}
#detail {{ position: absolute; top: 8px; right: 8px; width: 320px; max-height: calc(100% - 16px);
  background: #ffffff; border: 1px solid #cbd5e1; border-radius: 6px; padding: 12px;
  font-size: 13px; overflow-y: auto; display: none; }}
#detail h2 {{ font-size: 14px; margin-bottom: 8px; word-break: break-all; }}
#detail .field {{ margin-bottom: 4px; }}
#detail .label {{ color: #64748b; }}
#detail .close {{ position: absolute; top: 8px; right: 10px; cursor: pointer;
  color: #64748b; font-size: 16px; }}
#detail .close:hover {{ color: #334155; }}
</style>
</head>
<body>
<div id="header">
  <h1>csegraph — {repo_name}</h1>
  <span class="stats" id="stats"></span>
</div>
<div id="controls">
  <input id="search" type="text" placeholder="Filter nodes by name or path…">
  <div class="filter-group" id="rel-filters"></div>
</div>
<div id="canvas-wrap">
  <canvas id="graph"></canvas>
  <div id="detail">
    <span class="close" id="detail-close">&times;</span>
    <h2 id="d-name"></h2>
    <div class="field"><span class="label">ID:</span> <span id="d-id"></span></div>
    <div class="field"><span class="label">Kind:</span> <span id="d-kind"></span></div>
    <div class="field"><span class="label">Path:</span> <span id="d-path"></span></div>
    <div class="field" id="d-lines-wrap"><span class="label">Lines:</span> <span id="d-lines"></span></div>
    <div class="field" id="d-children-wrap"><span class="label">Children:</span> <span id="d-children"></span></div>
    <div class="field"><span class="label">Connections:</span> <span id="d-conn"></span></div>
  </div>
</div>
<script>
(function() {{
  var DATA = {data_json};
  var nodes = DATA.nodes, edges = DATA.edges;

  var KIND_COLORS = {{
    "file": "#2563eb", "folder": "#64748b", "repo": "#f97316",
    "class": "#7c3aed", "function": "#16a34a", "method": "#0284c7",
    "test": "#ea580c", "import": "#dc2626"
  }};
  var DEFAULT_COLOR = "#334155";

  document.getElementById("stats").textContent =
    nodes.length + " nodes, " + edges.length + " edges";

  var relations = {{}};
  edges.forEach(function(e) {{ relations[e.relation] = true; }});
  var relKeys = Object.keys(relations).sort();
  var activeRels = {{}};
  var filtersEl = document.getElementById("rel-filters");
  relKeys.forEach(function(r) {{
    activeRels[r] = true;
    var lbl = document.createElement("label");
    var cb = document.createElement("input");
    cb.type = "checkbox"; cb.checked = true;
    cb.addEventListener("change", function() {{ activeRels[r] = cb.checked; resetSim(); }});
    lbl.appendChild(cb); lbl.appendChild(document.createTextNode(" " + r));
    filtersEl.appendChild(lbl);
  }});

  var canvas = document.getElementById("graph");
  var ctx = canvas.getContext("2d");
  var W, H, dpr;
  function resize() {{
    var rect = canvas.parentElement.getBoundingClientRect();
    dpr = window.devicePixelRatio || 1;
    W = rect.width; H = rect.height;
    canvas.width = W * dpr; canvas.height = H * dpr;
    canvas.style.width = W + "px"; canvas.style.height = H + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }}
  resize();
  window.addEventListener("resize", function() {{ resize(); draw(); }});

  var INITIAL_SPREAD_X = Math.max(1400, W * 2.4);
  var INITIAL_SPREAD_Y = Math.max(900, H * 2.0);
  var INITIAL_ZOOM = Math.min(
    0.65,
    Math.max(0.28, Math.min(W / INITIAL_SPREAD_X, H / INITIAL_SPREAD_Y) * 1.2)
  );
  var camX = 0, camY = 0, camZ = INITIAL_ZOOM;
  var idxById = {{}};
  var sim = [];
  nodes.forEach(function(n, i) {{
    idxById[n.id] = i;
    sim.push({{
      x: (Math.random() - 0.5) * INITIAL_SPREAD_X,
      y: (Math.random() - 0.5) * INITIAL_SPREAD_Y,
      vx: 0, vy: 0, pinned: false
    }});
  }});

  var childrenById = {{}};
  var parentById = {{}};
  var expanded = {{}};
  nodes.forEach(function(n) {{
    if (n.parent_id && idxById[n.parent_id] !== undefined) {{
      parentById[n.id] = n.parent_id;
      if (!childrenById[n.parent_id]) childrenById[n.parent_id] = [];
      childrenById[n.parent_id].push(n.id);
    }}
  }});
  nodes.forEach(function(n) {{
    if (!n.parent_id || idxById[n.parent_id] === undefined) expanded[n.id] = true;
  }});

  var searchVal = "";
  document.getElementById("search").addEventListener("input", function(e) {{
    searchVal = e.target.value.toLowerCase(); draw();
  }});

  function visibleEdges() {{
    var visible = visibleNodeSet();
    return visibleEdgesFor(visible);
  }}

  function visibleEdgesFor(visible) {{
    return edges.filter(function(e) {{
      return activeRels[e.relation] && visible[e.source] && visible[e.target];
    }});
  }}

  function matchesSearch(n) {{
    if (!searchVal) return true;
    return n.name.toLowerCase().indexOf(searchVal) >= 0 ||
           n.path.toLowerCase().indexOf(searchVal) >= 0 ||
           n.id.toLowerCase().indexOf(searchVal) >= 0;
  }}

  function hasChildren(id) {{
    return !!(childrenById[id] && childrenById[id].length);
  }}

  function includeAncestors(visible, id) {{
    visible[id] = true;
    var parent = parentById[id];
    while (parent) {{
      visible[parent] = true;
      parent = parentById[parent];
    }}
  }}

  function visibleNodeSet() {{
    var visible = {{}};
    if (searchVal) {{
      nodes.forEach(function(n) {{
        if (matchesSearch(n)) includeAncestors(visible, n.id);
      }});
      return visible;
    }}

    function visit(id) {{
      visible[id] = true;
      if (!expanded[id]) return;
      (childrenById[id] || []).forEach(visit);
    }}

    nodes.forEach(function(n) {{
      if (!n.parent_id || idxById[n.parent_id] === undefined) visit(n.id);
    }});
    return visible;
  }}

  function visibleNodeIndices(visible) {{
    var indices = [];
    for (var i = 0; i < nodes.length; i++) {{
      if (visible[nodes[i].id]) indices.push(i);
    }}
    return indices;
  }}

  function toggleExpanded(i) {{
    var id = nodes[i].id;
    if (!hasChildren(id)) return;
    expanded[id] = !expanded[id];
  }}

  function toScreen(x, y) {{ return [(x - camX) * camZ + W/2, (y - camY) * camZ + H/2]; }}
  function toWorld(sx, sy) {{ return [(sx - W/2) / camZ + camX, (sy - H/2) / camZ + camY]; }}

  var selectedIdx = -1;
  var detailEl = document.getElementById("detail");
  document.getElementById("detail-close").addEventListener("click", function() {{
    selectedIdx = -1; detailEl.style.display = "none"; draw();
  }});

  function showDetail(i) {{
    var n = nodes[i];
    document.getElementById("d-name").textContent = n.name || n.id;
    document.getElementById("d-id").textContent = n.id;
    document.getElementById("d-kind").textContent = n.kind;
    document.getElementById("d-path").textContent = n.path;
    var lw = document.getElementById("d-lines-wrap");
    if (n.start_line != null) {{
      lw.style.display = "";
      document.getElementById("d-lines").textContent = n.start_line + "–" + (n.end_line || n.start_line);
    }} else {{ lw.style.display = "none"; }}
    var cw = document.getElementById("d-children-wrap");
    if (n.child_count) {{
      cw.style.display = "";
      document.getElementById("d-children").textContent =
        n.child_count + (expanded[n.id] ? " expanded" : " collapsed");
    }} else {{ cw.style.display = "none"; }}
    var conn = 0;
    edges.forEach(function(e) {{
      if (e.source === n.id || e.target === n.id) conn++;
    }});
    document.getElementById("d-conn").textContent = conn;
    detailEl.style.display = "block";
  }}

  var dragging = -1, dragOffX = 0, dragOffY = 0;
  var panning = false, panStartX = 0, panStartY = 0, panCamX = 0, panCamY = 0;
  canvas.addEventListener("mousedown", function(e) {{
    var rect = canvas.getBoundingClientRect();
    var mx = e.clientX - rect.left, my = e.clientY - rect.top;
    var hit = findNode(mx, my);
    if (hit >= 0) {{
      dragging = hit; sim[hit].pinned = true;
      var sp = toScreen(sim[hit].x, sim[hit].y);
      dragOffX = sp[0] - mx; dragOffY = sp[1] - my;
      selectedIdx = hit; toggleExpanded(hit); showDetail(hit); resetSim(); draw();
    }} else {{
      panning = true; panStartX = mx; panStartY = my;
      panCamX = camX; panCamY = camY;
    }}
  }});
  canvas.addEventListener("mousemove", function(e) {{
    var rect = canvas.getBoundingClientRect();
    var mx = e.clientX - rect.left, my = e.clientY - rect.top;
    if (dragging >= 0) {{
      var wp = toWorld(mx + dragOffX, my + dragOffY);
      sim[dragging].x = wp[0]; sim[dragging].y = wp[1];
      draw();
    }} else if (panning) {{
      camX = panCamX - (mx - panStartX) / camZ;
      camY = panCamY - (my - panStartY) / camZ;
      draw();
    }}
  }});
  canvas.addEventListener("mouseup", function() {{
    if (dragging >= 0) sim[dragging].pinned = false;
    dragging = -1; panning = false;
  }});
  canvas.addEventListener("wheel", function(e) {{
    e.preventDefault();
    var factor = e.deltaY > 0 ? 0.9 : 1.1;
    camZ = Math.max(0.05, Math.min(5, camZ * factor));
    draw();
  }}, {{ passive: false }});

  function findNode(mx, my) {{
    var best = -1, bestD = 36;
    var visible = visibleNodeSet();
    var indices = visibleNodeIndices(visible);
    for (var p = 0; p < indices.length; p++) {{
      var i = indices[p];
      var sp = toScreen(sim[i].x, sim[i].y);
      var dx = sp[0] - mx, dy = sp[1] - my;
      var d = dx*dx + dy*dy;
      if (d < bestD) {{ bestD = d; best = i; }}
    }}
    return best;
  }}

  var REPULSION = 2600, SPRING = 0.003, SPRING_LEN = 180, DAMPING = 0.88, CENTER = 0.00035;
  var simRunning = true, simTicks = 0, MAX_TICKS = 500;

  function stepSim() {{
    if (!simRunning || simTicks >= MAX_TICKS) return;
    simTicks++;
    var visible = visibleNodeSet();
    var visibleIndices = visibleNodeIndices(visible);
    var ve = visibleEdgesFor(visible);
    for (var a = 0; a < visibleIndices.length; a++) {{
      var i = visibleIndices[a];
      if (sim[i].pinned) continue;
      var fx = 0, fy = 0;
      for (var b = 0; b < visibleIndices.length; b++) {{
        var j = visibleIndices[b];
        if (i === j) continue;
        var dx = sim[i].x - sim[j].x, dy = sim[i].y - sim[j].y;
        var d2 = dx*dx + dy*dy;
        if (d2 < 1) d2 = 1;
        var f = REPULSION / d2;
        var dist = Math.sqrt(d2);
        fx += f * dx / dist; fy += f * dy / dist;
      }}
      for (var k = 0; k < ve.length; k++) {{
        var si = idxById[ve[k].source], ti = idxById[ve[k].target];
        if (si === undefined || ti === undefined) continue;
        var other = -1;
        if (si === i) other = ti; else if (ti === i) other = si;
        if (other < 0) continue;
        var dx2 = sim[other].x - sim[i].x, dy2 = sim[other].y - sim[i].y;
        var dist2 = Math.sqrt(dx2*dx2 + dy2*dy2) || 1;
        var f2 = SPRING * (dist2 - SPRING_LEN);
        fx += f2 * dx2 / dist2; fy += f2 * dy2 / dist2;
      }}
      fx += (0 - sim[i].x) * CENTER;
      fy += (0 - sim[i].y) * CENTER;
      sim[i].vx = (sim[i].vx + fx) * DAMPING;
      sim[i].vy = (sim[i].vy + fy) * DAMPING;
    }}
    for (var p = 0; p < visibleIndices.length; p++) {{
      var m = visibleIndices[p];
      if (!sim[m].pinned) {{
        sim[m].x += sim[m].vx;
        sim[m].y += sim[m].vy;
      }}
    }}
  }}

  function draw() {{
    ctx.clearRect(0, 0, W, H);
    var visible = visibleNodeSet();
    var visibleIndices = visibleNodeIndices(visible);
    var ve = visibleEdgesFor(visible);
    ctx.lineWidth = 0.5 / camZ;
    ctx.strokeStyle = "#cbd5e1";
    for (var i = 0; i < ve.length; i++) {{
      var si = idxById[ve[i].source], ti = idxById[ve[i].target];
      if (si === undefined || ti === undefined) continue;
      if (!matchesSearch(nodes[si]) && !matchesSearch(nodes[ti])) continue;
      var sp1 = toScreen(sim[si].x, sim[si].y);
      var sp2 = toScreen(sim[ti].x, sim[ti].y);
      ctx.beginPath(); ctx.moveTo(sp1[0], sp1[1]); ctx.lineTo(sp2[0], sp2[1]); ctx.stroke();
    }}
    var nodeR = Math.max(3, 5 * camZ);
    for (var p = 0; p < visibleIndices.length; p++) {{
      var j = visibleIndices[p];
      var sp = toScreen(sim[j].x, sim[j].y);
      if (sp[0] < -20 || sp[0] > W+20 || sp[1] < -20 || sp[1] > H+20) continue;
      ctx.beginPath(); ctx.arc(sp[0], sp[1], nodeR, 0, Math.PI*2);
      ctx.fillStyle = j === selectedIdx ? "#f97316" : (KIND_COLORS[nodes[j].kind] || DEFAULT_COLOR);
      ctx.fill();
      if (hasChildren(nodes[j].id)) {{
        ctx.strokeStyle = expanded[nodes[j].id] ? "#0f766e" : "#64748b";
        ctx.lineWidth = Math.max(1, 1.5 * camZ);
        ctx.stroke();
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = Math.max(1, 1.2 * camZ);
        ctx.beginPath();
        ctx.moveTo(sp[0] - nodeR * 0.45, sp[1]);
        ctx.lineTo(sp[0] + nodeR * 0.45, sp[1]);
        if (!expanded[nodes[j].id]) {{
          ctx.moveTo(sp[0], sp[1] - nodeR * 0.45);
          ctx.lineTo(sp[0], sp[1] + nodeR * 0.45);
        }}
        ctx.stroke();
      }}
      if (camZ > 0.6) {{
        ctx.fillStyle = "#334155";
        ctx.font = Math.max(9, 11 * camZ) + "px system-ui";
        ctx.fillText(nodes[j].name || nodes[j].id.split("::").pop(), sp[0] + nodeR + 3, sp[1] + 3);
      }}
    }}
  }}

  function resetSim() {{ simTicks = 0; simRunning = true; }}
  function loop() {{
    stepSim(); draw(); requestAnimationFrame(loop);
  }}
  loop();
}})();
</script>
</body>
</html>
"""
