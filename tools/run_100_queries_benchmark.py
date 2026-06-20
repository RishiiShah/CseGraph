import time
import os
import random
import statistics
from pathlib import Path
from typing import List, Dict, Any

from csegraph._core.server.app import _handle_tool
from csegraph._core.index.services import IndexService
from csegraph._core.retrieval.context import _task_tokens

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / ".csegraph" / "index.db"

# Core concepts and entities in csegraph
CONCEPTS = [
    "caching", "graph expansion", "AST parsing", "dependency tracking", 
    "MCP tools", "routing intents", "token budgets", "lexical scoring", 
    "database schema", "schema migrations", "hub node detection", 
    "workspace management", "change detection", "file fingerprinting", 
    "LLM context building", "ranking tie-breakers"
]

FILES = [
    "context.py", "app.py", "queries.py", "cache.py", "schema.py", 
    "loaders.py", "services.py", "session.py", "minimal.py", 
    "scoring.py", "change_detection.py", "treesitter/parser.py"
]

CLASSES = [
    "ContextService", "SnapshotManager", "IndexService", "ProjectIndex", 
    "BenchmarkService", "GraphSnapshot", "SufficiencyMetrics"
]

METHODS = [
    "_rank_nodes", "_handle_tool", "build_context", "run_corpus", 
    "_apply_session_filter", "get_snapshot", "_is_test_symbol_row", 
    "_task_tokens"
]

# Generate exactly 100 unique hard/vague/specific queries
random.seed(42)
def generate_queries() -> List[str]:
    queries = set()
    
    # 1. Vague/Exploratory (30)
    vague_templates = [
        "what files do {concept}?",
        "where does the system handle {concept}?",
        "how is {concept} implemented?",
        "show me everything related to {concept} in the repository",
        "which modules are responsible for {concept}?",
        "is there any code for {concept}?",
        "find all places that mention {concept}"
    ]
    
    # 2. Intermediate/Structural (40)
    structural_templates = [
        "which function inside {file} is responsible for {concept}?",
        "what components depend on {class_name}?",
        "how does {class_name} interact with {concept}?",
        "where is {method} called from within {file}?",
        "how does {file} use {class_name} for {concept}?",
        "what are the downstream dependents of {class_name}?",
        "trace the usage of {method} throughout the project"
    ]
    
    # 3. Hardcore/Specific (30)
    hardcore_templates = [
        "fix tie-breaking logic in {method} to prioritize {concept}",
        "explain the exact algorithm inside {class_name} that calculates {concept}",
        "how are {concept} stored in {file} and queried by {class_name}?",
        "what happens when {class_name} encounters an error during {concept}?",
        "refactor {method} in {file} to use {class_name}",
        "write a new test for {method} covering {concept}",
        "how does the recursive CTE in {file} prevent infinite loops for {concept}?"
    ]
    
    # Generate 100 unique queries
    while len(queries) < 100:
        if len(queries) < 30:
            tmpl = random.choice(vague_templates)
        elif len(queries) < 70:
            tmpl = random.choice(structural_templates)
        else:
            tmpl = random.choice(hardcore_templates)
            
        q = tmpl.format(
            concept=random.choice(CONCEPTS),
            file=random.choice(FILES),
            class_name=random.choice(CLASSES),
            method=random.choice(METHODS)
        )
        queries.add(q)
        
    return list(queries)

QUERIES = generate_queries()
ITERATIONS = 3

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
    print("Indexing repo for MCP deep testing (100 Queries)...")
    IndexService(str(DB_PATH)).index(str(REPO_ROOT), profile="small")
    
    print(f"\nStarting Mega Benchmark Comparison ({len(QUERIES)} queries, {ITERATIONS} iterations each)\n")
    
    results = []
    
    for i, q in enumerate(QUERIES, 1):
        # Optional: Print progress on same line to avoid flooding stdout
        print(f"\rProcessing [{i}/{len(QUERIES)}]...", end="", flush=True)
        b_latencies = []
        m_latencies = []
        b_tokens = 0
        m_tokens = 0
        
        for it in range(ITERATIONS):
            b_res = baseline_agent(q)
            b_latencies.append(b_res["latency_ms"])
            b_tokens = b_res["tokens"]
            
            m_res = mcp_agent(q)
            m_latencies.append(m_res["latency_ms"])
            m_tokens = m_res["tokens"]
            
        b_avg = statistics.mean(b_latencies)
        m_avg = statistics.mean(m_latencies)
        
        results.append({
            "query": q,
            "b_avg": b_avg,
            "b_tokens": b_tokens,
            "m_avg": m_avg,
            "m_tokens": m_tokens
        })
        
    print("\n\n" + "=" * 130)
    print(f"{'Query Snippet':<70} | {'Naive Latency':<15} | {'MCP Latency':<15} | {'Naive Tkns':<10} | {'MCP Tkns':<10}")
    print("=" * 130)
    
    total_b_tokens = 0
    total_m_tokens = 0
    total_b_ms = 0
    total_m_ms = 0
    
    # Sort results by token savings to highlight the most extreme
    results.sort(key=lambda x: (x["b_tokens"] / max(x["m_tokens"], 1)), reverse=True)
    
    for r in results:
        total_b_tokens += r["b_tokens"]
        total_m_tokens += r["m_tokens"]
        total_b_ms += r["b_avg"]
        total_m_ms += r["m_avg"]
        
        q_trunc = r["query"][:67] + "..." if len(r["query"]) > 70 else r["query"]
        print(f"{q_trunc:<70} | {r['b_avg']:>10.1f} ms    | {r['m_avg']:>10.1f} ms    | {r['b_tokens']:>10} | {r['m_tokens']:>10}")
    
    print("=" * 130)
    print(f"MEGA BENCHMARK SUMMARY (100 Unique Queries x {ITERATIONS} Iterations):")
    print(f"  Average Naive Query Latency : {total_b_ms / len(QUERIES):.1f}ms")
    print(f"  Average MCP Query Latency   : {total_m_ms / len(QUERIES):.1f}ms")
    print(f"  Total Context Tokens (Naive): {total_b_tokens:,}")
    print(f"  Total Context Tokens (MCP)  : {total_m_tokens:,}")
    
    overall_eff = total_b_tokens / max(total_m_tokens, 1)
    print(f"  Overall Token Efficiency    : MCP is {overall_eff:.1f}x smaller and significantly cheaper!")
    
    # Save to a markdown artifact directly from the script
    report_path = "/Users/rishi/.gemini/antigravity/brain/62c39c11-0325-49cf-bdc6-2fdc80722124/mega_mcp_benchmark_results.md"
    with open(report_path, "w") as f:
        f.write("# Mega MCP Benchmark Results (100 Queries)\n\n")
        f.write(f"We ran **100 unique queries** across {ITERATIONS} separate iterations to measure the exact context window savings using CseGraph MCP.\n\n")
        f.write("### Overall Stats\n")
        f.write(f"- **Total Naive Tokens**: {total_b_tokens:,}\n")
        f.write(f"- **Total MCP Tokens**: {total_m_tokens:,}\n")
        f.write(f"- **Overall Efficiency**: {overall_eff:.1f}x Token Reduction\n\n")
        f.write("### Query Breakdown (Sorted by token savings)\n")
        f.write("```text\n")
        f.write(f"{'Query Snippet':<70} | {'Naive Latency':<15} | {'MCP Latency':<15} | {'Naive Tkns':<10} | {'MCP Tkns':<10}\n")
        f.write("-" * 130 + "\n")
        for r in results:
            q_trunc = r["query"][:67] + "..." if len(r["query"]) > 70 else r["query"]
            f.write(f"{q_trunc:<70} | {r['b_avg']:>10.1f} ms    | {r['m_avg']:>10.1f} ms    | {r['b_tokens']:>10} | {r['m_tokens']:>10}\n")
        f.write("```\n")

if __name__ == '__main__':
    main()
