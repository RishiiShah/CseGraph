import os
import sys
# Add parent directory to sys path so that running from the agents directory works without PYTHONPATH
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ast
from typing import List
from models.code_element import CodeNode, FileNode, IngestionPayload, MethodNode

class IngestionAgent:
    def __init__(self, root_dir: str):
        self.root_dir = os.path.abspath(root_dir)
        self.excluded_dirs = {
            ".git",
            ".hg",
            ".svn",
            "__pycache__",
            "node_modules",
            "venv",
            ".venv",
            "env",
            "site-packages",
        }
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
        with open(file_path, 'r', encoding='utf-8') as f:
            source = f.read()
        
        try:
            tree = ast.parse(source, filename=file_path)
        except SyntaxError as e:
            print(f"Syntax error in {file_path}: {e}")
            return FileNode(file_path=file_path)

        lines = source.splitlines()
        
        imports = []
        nodes: List[CodeNode] = []
        
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.Import):
                    for n in node.names:
                        imports.append(n.name)
                elif isinstance(node, ast.ImportFrom):
                    prefix = '.' * node.level
                    module = f"{prefix}{node.module or ''}"
                    for n in node.names:
                        if module:
                            separator = '' if module.endswith('.') else '.'
                            import_name = f"{module}{separator}{n.name}"
                        else:
                            import_name = n.name
                        imports.append(import_name)

        imports = sorted(set(imports))
        
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                node_type = 'class' if isinstance(node, ast.ClassDef) else 'function'
                start_line = node.lineno
                end_line = getattr(node, 'end_lineno', start_line)
                code_content = "\n".join(lines[start_line-1:end_line])
                docstring = ast.get_docstring(node)
                
                children: List[MethodNode] = []
                if isinstance(node, ast.ClassDef):
                    for child in node.body:
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            c_start = child.lineno
                            c_end = getattr(child, 'end_lineno', c_start)
                            c_code = "\n".join(lines[c_start-1:c_end])
                            children.append(
                                MethodNode(
                                    name=child.name,
                                    start_line=c_start,
                                    end_line=c_end,
                                    docstring=ast.get_docstring(child),
                                    code_content=c_code,
                                )
                            )

                c_node = CodeNode(
                    name=node.name,
                    node_type=node_type,
                    file_path=file_path,
                    start_line=start_line,
                    end_line=end_line,
                    docstring=docstring,
                    code_content=code_content,
                    children=children
                )
                nodes.append(c_node)

        return FileNode(file_path=file_path, imports=imports, nodes=nodes)

    def ingest_repository(self) -> List[FileNode]:
        all_files = []
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
    agent = IngestionAgent(".")
    # Using relative path to ignore site-packages and big directories when testing
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
