"""Nuitka standalone build script for csegraph CLI.

Derives tree-sitter include packages from LANGUAGE_SPECS automatically.
Builds in --standalone mode (unpacked directory).

Usage:
    python build.py
    python build.py --output-dir dist
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _collect_treesitter_modules() -> list[str]:
    from csegraph_core.languages.treesitter.languages import LANGUAGE_SPECS

    modules: set[str] = {"tree_sitter"}
    for spec in LANGUAGE_SPECS:
        for loader in spec.loaders.values():
            modules.add(loader.module)
    return sorted(modules)


def build(output_dir: str = "dist") -> Path:
    entry = Path("packages/csegraph-cli/csegraph_cli/__main__.py")
    if not entry.exists():
        sys.exit(f"Entry point not found: {entry}")

    ts_modules = _collect_treesitter_modules()
    print(f"Derived {len(ts_modules)} tree-sitter packages from LANGUAGE_SPECS:")
    for m in ts_modules:
        print(f"  {m}")

    cmd: list[str] = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        f"--output-dir={output_dir}",
        "--output-filename=csegraph",
        "--include-package=csegraph_core",
        "--include-package=csegraph_cli",
        "--include-package-data=csegraph_core.graph.templates",
    ]

    for mod in ts_modules:
        cmd.append(f"--include-package={mod}")

    for dep in ("mcp", "watchfiles", "tomlkit"):
        cmd.append(f"--include-package={dep}")

    cmd += [
        "--python-flag=no_docstrings",
        "--python-flag=no_asserts",
        str(entry),
    ]

    print(f"\nRunning Nuitka ({len(cmd)} args)...")
    print(f"  {' '.join(cmd[:6])} ...")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        sys.exit(f"Nuitka build failed with exit code {result.returncode}")

    dist_dir = Path(output_dir) / "__main__.dist"
    binary = dist_dir / "csegraph"
    if not binary.exists():
        candidates = list(dist_dir.glob("csegraph*"))
        if candidates:
            binary = candidates[0]
        else:
            sys.exit(f"Binary not found in {dist_dir}")

    size_mb = binary.stat().st_size / (1024 * 1024)
    print(f"\nBuild complete.")
    print(f"  Binary: {binary.resolve()}")
    print(f"  Size:   {size_mb:.1f} MB")
    print(f"  Dir:    {dist_dir.resolve()}")
    print(f"\nRun verification:")
    print(f"  python scripts/verify_binary.py {binary.resolve()}")

    return binary.resolve()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build csegraph standalone binary via Nuitka")
    parser.add_argument("--output-dir", default="dist", help="Output directory (default: dist)")
    args = parser.parse_args()
    build(args.output_dir)


if __name__ == "__main__":
    main()
