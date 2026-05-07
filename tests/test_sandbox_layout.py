from pathlib import Path


def test_sandboxes_are_not_nested_under_tests():
    project_root = Path(__file__).resolve().parents[1]

    assert (project_root / "sandboxes" / "graph_analytics").is_dir()
    assert not (project_root / "tests" / "fixtures" / "sandboxes").exists()
