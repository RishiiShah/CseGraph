from pathlib import Path
from types import SimpleNamespace

from csegraph_codegen import CodegenResult, CodegenService
from csegraph_core.cse.metrics import SufficiencyMetrics
from csegraph_core.index.services import IndexService


def _write_sample_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "utils.py").write_text(
        "\n".join(
            [
                "def format_user(name: str) -> str:",
                '    """Normalize a display name."""',
                "    return name.strip().title()",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (root / "main.py").write_text(
        "\n".join(
            [
                "from utils import format_user",
                "",
                "def build_report(name: str) -> str:",
                '    """Build a simple user report."""',
                "    return f'Report: {format_user(name)}'",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_codegen_result_construction():
    result = CodegenResult(
        command="codegen",
        db_path="/tmp/test.db",
        repo_root="/tmp/repo",
        profile="medium",
        task="Add a calculator function",
        target_node_id="symbol::calc.py::function::add",
        model="stub-model",
        generated_code="def add(a, b): return a + b",
        is_sufficient=True,
        metrics=SufficiencyMetrics(
            dependency_completeness=1.0,
            entity_coverage=1.0,
            semantic_overlap=0.8,
            model_confidence=0.9,
        ),
        context_nodes_used=["node_a", "node_b"],
        raw_code_nodes_used=[],
        prompt_tokens=100,
        completion_tokens=50,
        elapsed_seconds=1.23,
    )
    assert result.command == "codegen"
    assert result.model == "stub-model"
    assert result.generated_code == "def add(a, b): return a + b"
    assert result.metrics.dependency_completeness == 1.0
    assert result.elapsed_seconds == 1.23
    assert result.output_path is None


def test_codegen_service_generate_with_mocked_groq(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "repo.csegraph.db"
    _write_sample_repo(repo)
    IndexService(db_path).index(repo, profile="small")

    svc = CodegenService.__new__(CodegenService)
    svc.db_path = str(db_path)
    svc.model = "test-mock"
    svc._temperature = 0.2
    svc._max_tokens = 2048
    svc._local_llm = None
    svc._groq_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_kwargs: SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content="```python\ndef build_report(name): return name\n```"
                            )
                        )
                    ],
                    usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50),
                )
            )
        )
    )

    result = svc.generate(
        task="Implement build_report",
        target="symbol::main.py::function::build_report",
        profile="small",
    )

    assert result.command == "codegen"
    assert result.model == "test-mock"
    assert result.generated_code == "def build_report(name): return name"
    assert result.target_node_id == "symbol::main.py::function::build_report"
    assert result.context_nodes_used
    assert result.prompt_tokens == 100
    assert result.completion_tokens == 50
