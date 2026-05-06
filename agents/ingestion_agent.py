import os
import sys
from pathlib import Path
from typing import List

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from csegraph.languages.python.parser import EXCLUDED_DIRS, parse_python_file
from csegraph.legacy.adapters import parsed_file_to_filenode
from models.code_element import FileNode, IngestionPayload


class IngestionAgent:
    """Legacy wrapper around csegraph.languages.python.parser.

    Preserves the original public API (`extract_nodes_from_file`,
    `ingest_repository`, `save_to_json`) and the JSON shape produced by
    `IngestionPayload.model_dump()` so that downstream agents and
    `run_pipeline.py` continue to work byte-stably.
    """

    def __init__(self, root_dir: str):
        self.root_dir = os.path.abspath(root_dir)
        self.excluded_dirs = set(EXCLUDED_DIRS)
        self.excluded_filenames = {
            ".env",
            ".env.local",
            ".env.development",
            ".env.test",
            ".env.production",
            ".python-version",
        }
        self.excluded_suffixes = {
            ".pyc",
            ".pyo",
        }

    def _is_excluded_path(self, path: str) -> bool:
        parts = set(path.split(os.sep))
        return any(part in parts for part in self.excluded_dirs)

    def _is_excluded_file(self, filename: str) -> bool:
        if filename in self.excluded_filenames:
            return True
        if filename.startswith('.'):
            return True
        return any(filename.endswith(suffix) for suffix in self.excluded_suffixes)

    def extract_nodes_from_file(self, file_path: str) -> FileNode:
        try:
            parsed = parse_python_file(Path(file_path), Path(self.root_dir))
        except (OSError, UnicodeDecodeError) as exc:
            print(f"Could not read {file_path}: {exc}")
            return FileNode(file_path=file_path)

        if parsed.parse_status != "ok":
            print(f"Syntax error in {file_path}: {parsed.parse_error}")
            return FileNode(file_path=file_path)

        return parsed_file_to_filenode(parsed)

    def ingest_repository(self) -> List[FileNode]:
        all_files: List[FileNode] = []
        for root, dirs, files in os.walk(self.root_dir):
            dirs[:] = sorted(
                d for d in dirs if d not in self.excluded_dirs and not d.startswith('.')
            )
            if self._is_excluded_path(root):
                continue

            for file in sorted(files):
                if self._is_excluded_file(file):
                    continue
                if file.endswith('.py'):
                    full_path = os.path.join(root, file)
                    if self._is_excluded_path(full_path):
                        continue
                    all_files.append(self.extract_nodes_from_file(full_path))
        return sorted(all_files, key=lambda file_node: file_node.file_path)

    def save_to_json(self, files: List[FileNode], output_path: str):
        import json
        payload = IngestionPayload(root_dir=self.root_dir, files=files)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(payload.model_dump(), f, indent=4)


if __name__ == "__main__":
    default_root = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "sandboxes",
        "baseline_import_resolution",
    )
    agent = IngestionAgent(default_root)
    parsed_files = agent.ingest_repository()

    output_dir = "data"
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, "ingested_data.json")

    agent.save_to_json(parsed_files, output_file)

    print(f"Ingested {len(parsed_files)} Python files. Detailed map saved to '{output_file}'\n")
    for pf in parsed_files:
        print(f"=== File: {pf.file_path} ===")
        if pf.imports:
            print(f"  Imports: {', '.join(pf.imports)}")
        for node in pf.nodes:
            print(f"  - [{node.node_type.upper()}] {node.name} (Lines {node.start_line}-{node.end_line})")
            for child in node.children:
                print(
                    f"      -> [METHOD] {child.name} "
                    f"(Lines {child.start_line}-{child.end_line})"
                )
        print()
