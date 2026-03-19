import os
import sys
# Add parent directory to sys path so that running from the agents directory works without PYTHONPATH
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ast
from typing import List
from models.code_element import CodeNode, FileNode

class IngestionAgent:
    def __init__(self, root_dir: str):
        self.root_dir = root_dir

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
                    module = node.module or ''
                    for n in node.names:
                        imports.append(f"{module}.{n.name}")
        
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                node_type = 'class' if isinstance(node, ast.ClassDef) else 'function'
                start_line = node.lineno
                end_line = getattr(node, 'end_lineno', start_line)
                code_content = "\n".join(lines[start_line-1:end_line])
                docstring = ast.get_docstring(node)
                
                children = []
                if isinstance(node, ast.ClassDef):
                    for child in node.body:
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            c_start = child.lineno
                            c_end = getattr(child, 'end_lineno', c_start)
                            c_code = "\n".join(lines[c_start-1:c_end])
                            children.append({
                                'name': child.name,
                                'node_type': 'method',
                                'start_line': c_start,
                                'end_line': c_end,
                                'docstring': ast.get_docstring(child),
                                'code_content': c_code
                            })

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
        for root, _, files in os.walk(self.root_dir):
            for file in files:
                if file.endswith('.py'):
                    # Skip hidden directories and virtual environments
                    if any(part.startswith('.') and part != '.' for part in root.split(os.sep)) or 'venv' in root.split(os.sep):
                        continue
                    full_path = os.path.join(root, file)
                    all_files.append(self.extract_nodes_from_file(full_path))
        return all_files

    def save_to_json(self, files: List[FileNode], output_path: str):
        import json
        with open(output_path, 'w', encoding='utf-8') as f:
            data = [pf.model_dump() for pf in files]
            json.dump(data, f, indent=4)

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
                print(f"      -> [METHOD] {child['name']} (Lines {child['start_line']}-{child['end_line']})")
        print()
