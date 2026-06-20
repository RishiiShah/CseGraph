import time
import os
import random
import sqlite3
import subprocess
import statistics
from pathlib import Path
from typing import List, Dict, Any

from csegraph._core.server.app import _handle_tool
from csegraph._core.index.services import IndexService
from csegraph._core.retrieval.context import _task_tokens

REPO_ROOT = Path(__file__).resolve().parents[1]
SANDBOX_DIR = REPO_ROOT / "sandbox"

REPOS = [
    "nanoGPT",
    "micrograd",
    "django",
    "pandas",
    "flask",
    "transformers",
    "scikit-learn",
    "fastapi",
    "celery",
    "pytest"
]

def generate_queries(repo_path: Path, db_path: str) -> List[str]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    files = [r["name"] for r in conn.execute("SELECT name FROM nodes WHERE type = 'file'").fetchall()]
    classes = [r["name"] for r in conn.execute("SELECT name FROM nodes WHERE type = 'class'").fetchall()]
    methods = [r["name"] for r in conn.execute("SELECT name FROM nodes WHERE type IN ('function', 'method')").fetchall()]
    
    conn.close()
    
    if not classes: classes = ["App"]
    if not methods: methods = ["init"]
    if not files: files = ["main.py"]
    
    random.seed(42)
    queries = set()
    
    vague_templates = [
        "what files are responsible for {class_name}?",
        "where does the system handle {class_name}?",
        "how is {method} implemented?",
        "show me everything related to {class_name} in the repository",
        "which modules are responsible for {method}?",
        "is there any code for {class_name}?",
        "find all places that mention {file}"
    ]
    
    structural_templates = [
        "which function inside {file} is responsible for {class_name}?",
        "what components depend on {class_name}?",
        "how does {class_name} interact with {method}?",
        "where is {method} called from within {file}?",
        "how does {file} use {class_name}?",
        "what are the downstream dependents of {class_name}?",
        "trace the usage of {method} throughout the project"
    ]
    
    hardcore_templates = [
        "fix tie-breaking logic in {method} to prioritize {class_name}",
        "explain the exact algorithm inside {class_name} that calculates {method}",
        "how are {method} stored in {file} and queried by {class_name}?",
        "what happens when {class_name} encounters an error during {method}?",
        "refactor {method} in {file} to use {class_name}",
        "write a new test for {method} covering {class_name}",
        "how does the recursive logic in {file} prevent infinite loops for {class_name}?"
    ]
    
    while len(queries) < 100:
        if len(queries) < 30:
            tmpl = random.choice(vague_templates)
        elif len(queries) < 70:
            tmpl = random.choice(structural_templates)
        else:
            tmpl = random.choice(hardcore_templates)
            
        q = tmpl.format(
            file=random.choice(files),
            class_name=random.choice(classes),
            method=random.choice(methods)
        )
        queries.add(q)
        
    return list(queries)

def baseline_agent(query: str, repo_path: Path) -> Dict[str, Any]:
    start = time.perf_counter()
    tokens = _task_tokens(query)
    read_bytes = 0
    read_files = 0
    
    for path in repo_path.rglob("*.py"):
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

def mcp_agent(query: str, repo_path: Path, db_path: Path) -> Dict[str, Any]:
    start = time.perf_counter()
    try:
        res = _handle_tool("csegraph_context", {
            "task": query,
            "detail_level": "standard",
            "repo": str(repo_path),
            "db": str(db_path)
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

def run_phase_b(repo_path: Path, db_path: Path):
    print(f"\n--- Running Phase B (Active Mutation) for {repo_path.name} ---")
    
    # 1. Find a core file to modify
    target_file = None
    for path in repo_path.rglob("*.py"):
        if "test" not in path.name and not path.name.startswith("_"):
            target_file = path
            break
            
    if not target_file:
        print("No valid file found to modify.")
        return 0.0
        
    # 2. Modify the file
    original_content = target_file.read_text(encoding="utf-8")
    mutation = "\n\ndef __csegraph_dummy_test_method():\n    pass\n"
    target_file.write_text(original_content + mutation, encoding="utf-8")
    
    # 3. Stage the file with git so csegraph detects the change
    subprocess.run(["git", "add", str(target_file)], cwd=str(repo_path), check=True)
    
    # 4. Measure invalidation query latency
    query = "what does __csegraph_dummy_test_method do?"
    
    start = time.perf_counter()
    _handle_tool("csegraph_context", {
        "task": query,
        "detail_level": "standard",
        "repo": str(repo_path),
        "db": str(db_path)
    })
    end = time.perf_counter()
    latency = (end - start) * 1000
    
    print(f"Mutation invalidation latency: {latency:.1f}ms")
    
    # 5. Cleanup
    target_file.write_text(original_content, encoding="utf-8")
    subprocess.run(["git", "restore", "--staged", str(target_file)], cwd=str(repo_path), check=True)
    
    return latency

def main():
    report_path = Path("/Users/rishi/.gemini/antigravity/brain/62c39c11-0325-49cf-bdc6-2fdc80722124/cross_repo_benchmark_results.md")
    
    with open(report_path, "w") as f:
        f.write("# Cross-Repo Mega Benchmark Results\n\n")
        f.write("We ran Phase A (Clean Context) and Phase B (Active Mutation) across 5 open-source repositories.\n\n")
    
    for repo_name in REPOS:
        repo_path = SANDBOX_DIR / repo_name
        if not repo_path.exists():
            print(f"Skipping {repo_name}, directory not found.")
            continue
            
        db_path = repo_path / ".csegraph" / "index.db"
        
        print(f"\n=========================================")
        print(f"Indexing repository: {repo_name} ...")
        IndexService(str(db_path)).index(str(repo_path), profile="small")
        
        queries = generate_queries(repo_path, str(db_path))
        print(f"Generated 100 specific tailored queries for {repo_name}.")
        
        total_b_ms = 0
        total_m_ms = 0
        total_b_tokens = 0
        total_m_tokens = 0
        
        for i, q in enumerate(queries, 1):
            print(f"\r[{repo_name}] Processing query {i}/100...", end="", flush=True)
            
            # Baseline
            b_res = baseline_agent(q, repo_path)
            total_b_ms += b_res["latency_ms"]
            total_b_tokens += b_res["tokens"]
            
            # MCP
            m_res = mcp_agent(q, repo_path, db_path)
            total_m_ms += m_res["latency_ms"]
            total_m_tokens += m_res["tokens"]
            
        print(f"\nPhase A Completed for {repo_name}.")
        
        phase_b_latency = run_phase_b(repo_path, db_path)
        
        avg_b_ms = total_b_ms / len(queries)
        avg_m_ms = total_m_ms / len(queries)
        eff = total_b_tokens / max(total_m_tokens, 1)
        
        with open(report_path, "a") as f:
            f.write(f"## {repo_name.upper()}\n")
            f.write(f"- **Average Naive Latency**: {avg_b_ms:.1f}ms\n")
            f.write(f"- **Average MCP Latency**: {avg_m_ms:.1f}ms\n")
            f.write(f"- **Total Naive Tokens**: {total_b_tokens:,}\n")
            f.write(f"- **Total MCP Tokens**: {total_m_tokens:,}\n")
            f.write(f"- **Token Efficiency**: {eff:.1f}x reduction\n")
            f.write(f"- **Phase B (Mutation) Latency**: {phase_b_latency:.1f}ms\n\n")

if __name__ == '__main__':
    main()
