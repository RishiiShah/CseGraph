import time
import os
import statistics
from pathlib import Path
from typing import List, Dict, Any

from csegraph._core.server.app import _handle_tool
from csegraph._core.index.services import IndexService
from csegraph._core.retrieval.context import _task_tokens

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / ".csegraph" / "index.db"

QUERIES = [
    # 1. Simple lookup
    "Find all MCP tools registered",
    
    # 2. Algorithmic lookup
    "How does ContextService rank nodes and what tie-breaking logic does it use?",
    
    # 3. Multi-hop structural dependency
    "Where is the SQLite connection initialized and how does SnapshotManager use it?",
    
    # 4. Actionable specific refactor
    "Fix tie-breaking in _rank_nodes in context.py to prioritize symbols over files",
    
    # 5. Heavy structural dependency
    "What components depend on the csegraph_minimal tool and how does it route intents?",
    
    # 6. Deep architectural summary
    "Explain the full lifecycle of a GraphSnapshot from cache miss to eviction"
]

ITERATIONS = 5

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
    
    try:
        res = _handle_tool("csegraph_context", {
            "task": query,
            "detail_level": "standard",
            "repo": str(REPO_ROOT)
        })
        text = str(res)
        read_bytes = len(text)
    except Exception as e:
        read_bytes = 0
        print(f"Error during MCP for query '{query}': {e}")

    end = time.perf_counter()
    return {
        "latency_ms": (end - start) * 1000,
        "bytes": read_bytes,
        "tokens": read_bytes // 4,
        "files": "N/A (graph)"
    }

def main():
    print("Indexing repo for MCP deep testing...")
    IndexService(str(DB_PATH)).index(str(REPO_ROOT), profile="small")
    
    print(f"\nStarting Deep Benchmark Comparison ({ITERATIONS} iterations per query)\n")
    
    results = []
    
    for i, q in enumerate(QUERIES, 1):
        print(f"[{i}/{len(QUERIES)}] Query: {q}")
        b_latencies = []
        m_latencies = []
        b_tokens = 0
        m_tokens = 0
        
        for it in range(ITERATIONS):
            # Baseline
            b_res = baseline_agent(q)
            b_latencies.append(b_res["latency_ms"])
            b_tokens = b_res["tokens"]  # Deterministic size
            
            # MCP
            m_res = mcp_agent(q)
            m_latencies.append(m_res["latency_ms"])
            m_tokens = m_res["tokens"]  # Deterministic size
            
        b_avg = statistics.mean(b_latencies)
        b_std = statistics.stdev(b_latencies) if len(b_latencies) > 1 else 0
        
        m_avg = statistics.mean(m_latencies)
        m_std = statistics.stdev(m_latencies) if len(m_latencies) > 1 else 0
        
        results.append({
            "query": q,
            "b_avg": b_avg,
            "b_std": b_std,
            "b_tokens": b_tokens,
            "m_avg": m_avg,
            "m_std": m_std,
            "m_tokens": m_tokens
        })
        
        print(f"  Baseline : {b_avg:7.1f}ms (±{b_std:5.1f}) | {b_tokens:7} tokens")
        print(f"  MCP      : {m_avg:7.1f}ms (±{m_std:5.1f}) | {m_tokens:7} tokens")
        if m_tokens > 0:
            print(f"  Savings  : {b_tokens / m_tokens:7.1f}x tokens\n")
        else:
            print(f"  Savings  : N/A\n")

    print("-" * 120)
    print(f"{'Query':<80} | {'Naive (ms)' :<10} | {'MCP (ms)' :<10} | {'Token Savings' :<15}")
    print("-" * 120)
    
    total_b_tokens = 0
    total_m_tokens = 0
    total_b_ms = 0
    total_m_ms = 0
    
    for r in results:
        total_b_tokens += r["b_tokens"]
        total_m_tokens += r["m_tokens"]
        total_b_ms += r["b_avg"]
        total_m_ms += r["m_avg"]
        
        savings = f"{r['b_tokens']/r['m_tokens']:.1f}x" if r['m_tokens'] > 0 else "N/A"
        q_trunc = r["query"][:78] + ".." if len(r["query"]) > 80 else r["query"]
        print(f"{q_trunc:<80} | {r['b_avg']:<10.1f} | {r['m_avg']:<10.1f} | {savings:<15}")
    
    print("-" * 120)
    if total_m_tokens > 0:
        print(f"OVERALL SUMMARY:")
        print(f"  Average Query Latency (Naive) : {total_b_ms / len(QUERIES):.1f}ms")
        print(f"  Average Query Latency (MCP)   : {total_m_ms / len(QUERIES):.1f}ms")
        print(f"  Total Context Tokens (Naive)  : {total_b_tokens:,}")
        print(f"  Total Context Tokens (MCP)    : {total_m_tokens:,}")
        print(f"  Overall Token Efficiency      : {total_b_tokens / total_m_tokens:.1f}x smaller context footprint")

if __name__ == '__main__':
    main()
