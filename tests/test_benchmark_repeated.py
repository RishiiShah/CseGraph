import json
import os
import tempfile
import unittest
from unittest.mock import patch

from benchmark_repeated import run_repeated_benchmark


class TestBenchmarkRepeatedIsolation(unittest.TestCase):
    def test_runs_execute_in_isolated_subprocess_outputs(self) -> None:
        fixture_root = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "sandboxes",
        )

        output_json_paths: list[str] = []

        def fake_subprocess_run(command, cwd, check, capture_output, text):
            self.assertTrue(check)
            self.assertTrue(capture_output)
            self.assertTrue(text)
            self.assertTrue(command[1].endswith("benchmark_sandboxes.py"))

            json_path = command[command.index("--output-json") + 1]
            csv_path = command[command.index("--output-csv") + 1]
            output_json_paths.append(json_path)

            sample_result = [
                {
                    "sandbox": "demo",
                    "path": fixture_root,
                    "metrics": {
                        "file_count": 1,
                        "symbol_count": 1,
                        "node_count": 2,
                        "edge_count": 1,
                        "import_edges": 0,
                        "call_edges": 0,
                        "contains_edges": 1,
                        "imports_in_source": 0,
                        "resolved_import_ratio": 0.0,
                        "symbols_per_file": 1.0,
                        "edges_per_node": 0.5,
                        "ingestion_seconds": 0.001,
                        "linking_seconds": 0.001,
                        "total_seconds": 0.002,
                    },
                }
            ]

            with open(json_path, "w", encoding="utf-8") as run_file:
                json.dump(sample_result, run_file)
            with open(csv_path, "w", encoding="utf-8") as csv_file:
                csv_file.write("sandbox,file_count\n")
                csv_file.write("demo,1\n")

            class Completed:
                returncode = 0
                stdout = "ok"
                stderr = ""

            return Completed()

        with patch("benchmark_repeated.subprocess.run", side_effect=fake_subprocess_run) as mock_run:
            payload = run_repeated_benchmark(fixture_root, repeats=3)

        self.assertEqual(mock_run.call_count, 3)
        self.assertEqual(len(payload["runs"]), 3)
        self.assertEqual(len(set(output_json_paths)), 3)
        self.assertEqual(payload["summary"][0]["repeats"], 3)


if __name__ == "__main__":
    unittest.main()
