import time
import os
from pathlib import Path
from typing import List, Dict, Any

from csegraph._core.server.app import _handle_tool
from csegraph._core.index.services import IndexService
from csegraph._core.retrieval.context import _task_tokens

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / ".csegraph" / "index.db"

QUERIES = [
    "Find all MCP tools registered",
    "How does ContextService rank nodes?",
    "Where is the SQLite connection initialized?",
    "Fix tie-breaking in _rank_nodes in context.py",
]

def baseline_agent(query: str) -> Dict[str, Any]:
    start = time.perf_counter()
    tokens = _task_tokens(query)
    read_bytes = 0
    read_files = 0
    
    # Simulate an agent doing 'grep_search' or reading full files matching terms
    for path in REPO_ROOT.rglob("*.py"):
        if ".env" in str(path) or "env/" in str(path) or ".venv" in str(path):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            continue
        
        # Simple simulated lexical match
        content_lower = content.lower()
        if any(t in content_lower for t in tokens if len(t) > 3):
            read_bytes += len(content)
            read_files += 1

    end = time.perf_counter()
    return {
        "latency_ms": (end - start) * 1000,
        "bytes": read_bytes,
        "tokens": read_bytes // 4,
        "files": read_files
    }

def mcp_agent(query: str) -> Dict[str, Any]:
    start = time.perf_counter()
    
    # Call csegraph_context directly as if via MCP
    try:
        import json
        res = _handle_tool("csegraph_context", {
            "task": query,
            "detail_level": "standard",
            "repo": str(REPO_ROOT)
        })
        text = str(res)
        read_bytes = len(text)
    except Exception as e:
        print(f"Error during MCP: {e}")
        read_bytes = 0
        text = f"Error: {e}"

    end = time.perf_counter()
    return {
        "latency_ms": (end - start) * 1000,
        "bytes": read_bytes,
        "tokens": read_bytes // 4,
        "files": "N/A (graph)"
    }

def main():
    print("Indexing repo for MCP...")
    IndexService(str(DB_PATH)).index(str(REPO_ROOT), profile="small")
    
    print("\nStarting Benchmark Comparison (Naive Agent vs CseGraph MCP)\n")
    print(f"{'Query':<50} | {'Baseline (Naive)' :<30} | {'CseGraph (MCP)' :<30}")
    print("-" * 115)
    
    total_baseline_tokens = 0
    total_mcp_tokens = 0
    total_baseline_ms = 0
    total_mcp_ms = 0
    
    for q in QUERIES:
        b_res = baseline_agent(q)
        m_res = mcp_agent(q)
        
        total_baseline_tokens += b_res["tokens"]
        total_mcp_tokens += m_res["tokens"]
        total_baseline_ms += b_res["latency_ms"]
        total_mcp_ms += m_res["latency_ms"]
        
        b_str = f"{b_res['latency_ms']:.0f}ms, {b_res['tokens']} tkns"
        m_str = f"{m_res['latency_ms']:.0f}ms, {m_res['tokens']} tkns"
        
        print(f"{q[:48]:<50} | {b_str:<30} | {m_str:<30}")
    
    print("-" * 115)
    print(f"{'TOTAL':<50} | {total_baseline_ms:.0f}ms, {total_baseline_tokens} tkns | {total_mcp_ms:.0f}ms, {total_mcp_tokens} tkns")
    if total_mcp_tokens > 0:
        print(f"\nResults: MCP saved {total_baseline_tokens / total_mcp_tokens:.1f}x tokens and was {total_baseline_ms / total_mcp_ms:.1f}x faster!")

if __name__ == '__main__':
    main()
