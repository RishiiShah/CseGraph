"""Pinned open-source repository profiles for agent-aware benchmarks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from tools.benchmarks.agent import RepositoryAgentProfile
from tools.benchmarks.models import BenchmarkRepository

_CATEGORIES = (
    "definition",
    "ambiguous",
    "cross-file",
    "debug",
    "refactor",
    "structural",
    "test-impact",
)


@dataclass(frozen=True)
class SandboxRepositorySpec:
    name: str
    path: str
    url: str
    commit: str
    size_tier: str
    license_id: str
    source_globs: tuple[str, ...]
    source_roots: tuple[str, ...]
    test_roots: tuple[str, ...]
    query_budgets: dict[str, int]
    read_budgets: dict[str, int]
    read_windows: tuple[int, ...]
    scenario_templates: tuple[tuple[str, str], ...]
    token_budgets: dict[str, int]
    language: str = "python"

    def repository(self) -> BenchmarkRepository:
        return BenchmarkRepository(path=self.path, url=self.url, commit=self.commit)

    def agent_profile(self) -> RepositoryAgentProfile:
        return RepositoryAgentProfile(
            name=self.name,
            source_globs=self.source_globs,
            search_roots=self.source_roots,
            test_roots=self.test_roots,
            query_budgets=self.query_budgets,
            read_budgets=self.read_budgets,
            read_windows=self.read_windows,
            match_budgets={
                category: max(8, self.query_budgets[category] * 8)
                for category in self.query_budgets
            },
            language=self.language,
        )


def _spec(
    name: str,
    path: str,
    url: str,
    commit: str,
    tier: str,
    license_id: str,
    *,
    source_roots: tuple[str, ...],
    test_roots: tuple[str, ...],
    query_budgets: tuple[int, ...],
    read_budgets: tuple[int, ...],
    source_globs: tuple[str, ...] = ("*.py",),
    language: str = "python",
    scenario_templates: tuple[tuple[str, str], ...] = (),
) -> SandboxRepositorySpec:
    resolved_templates = scenario_templates or _REPO_SCENARIOS[name]
    return SandboxRepositorySpec(
        name=name,
        path=path,
        url=url,
        commit=commit,
        size_tier=tier,
        license_id=license_id,
        source_globs=source_globs,
        source_roots=source_roots,
        test_roots=test_roots,
        query_budgets=dict(zip(_CATEGORIES, query_budgets, strict=True)),
        read_budgets=dict(zip(_CATEGORIES, read_budgets, strict=True)),
        read_windows=(24, 48, 96, 160),
        scenario_templates=resolved_templates,
        token_budgets={
            category: 800 + query * 64
            for category, query in zip(_CATEGORIES, query_budgets, strict=True)
        },
        language=language,
    )


_REPO_SCENARIOS: dict[str, tuple[tuple[str, str], ...]] = {
    "micrograd": (
        ("definition", "Explain the autograd operation around {name}"),
        ("cross-file", "Trace how {name} participates in graph construction"),
        ("debug", "Inspect the gradient behavior exercised by {name}"),
        ("test-impact", "Find tests and callers affected by {name}"),
    ),
    "nanoGPT": (
        ("definition", "Explain the training or model helper {name}"),
        ("cross-file", "Trace the data flow around {name} in the training path"),
        ("structural", "Trace the model configuration flow around {name}"),
        ("test-impact", "Find usage and validation paths for {name}"),
    ),
    "click": (
        ("definition", "Explain the command-line behavior implemented by {name}"),
        ("cross-file", "Trace command parsing and invocation around {name}"),
        ("debug", "Inspect error handling and tests related to {name}"),
        ("test-impact", "Find tests and callers affected by {name}"),
    ),
    "requests": (
        ("definition", "Explain the HTTP client behavior implemented by {name}"),
        ("cross-file", "Trace request preparation and response handling around {name}"),
        ("debug", "Inspect error handling and tests related to {name}"),
        ("test-impact", "Find tests and callers affected by {name}"),
    ),
    "flask": (
        ("definition", "Explain the routing or application behavior in {name}"),
        ("cross-file", "Trace request dispatch and context flow around {name}"),
        ("structural", "Trace blueprint or CLI registration around {name}"),
        ("test-impact", "Find dispatch tests and callers affected by {name}"),
    ),
    "fastapi": (
        ("definition", "Explain dependency injection or routing behavior in {name}"),
        ("cross-file", "Trace validation and OpenAPI flow around {name}"),
        ("structural", "Trace the async request path around {name}"),
        ("test-impact", "Find test-client coverage and callers for {name}"),
    ),
    "pytest": (
        ("definition", "Explain collection or fixture behavior in {name}"),
        ("cross-file", "Trace plugin and hook flow around {name}"),
        ("structural", "Trace the test execution path around {name}"),
        ("test-impact", "Find regression tests and callers for {name}"),
    ),
    "celery": (
        ("definition", "Explain task registration or worker behavior in {name}"),
        ("cross-file", "Trace task routing and result flow around {name}"),
        ("structural", "Trace worker or beat control around {name}"),
        ("test-impact", "Find integration tests and callers for {name}"),
    ),
    "django": (
        ("definition", "Explain URL, model, form, or admin behavior in {name}"),
        ("cross-file", "Trace framework request or query flow around {name}"),
        ("structural", "Trace the subsystem architecture around {name}"),
        ("test-impact", "Find framework tests and callers affected by {name}"),
    ),
    "scikit-learn": (
        ("definition", "Explain estimator or validation behavior in {name}"),
        ("cross-file", "Trace pipeline and model-selection flow around {name}"),
        ("structural", "Trace dataset or metric dispatch around {name}"),
        ("test-impact", "Find estimator tests and callers for {name}"),
    ),
    "pandas": (
        ("definition", "Explain dataframe, dtype, or IO behavior in {name}"),
        ("cross-file", "Trace dispatch and data flow around {name}"),
        ("structural", "Trace the internal array or groupby architecture around {name}"),
        ("test-impact", "Find regression tests and callers affected by {name}"),
    ),
    "transformers": (
        ("definition", "Explain model, tokenizer, or generation behavior in {name}"),
        ("cross-file", "Trace config loading and model flow around {name}"),
        ("structural", "Trace pipeline or serving architecture around {name}"),
        ("test-impact", "Find model tests and callers affected by {name}"),
    ),
}


SANDBOX_REPOSITORIES = (
    _spec(
        "micrograd",
        "sandbox/micrograd",
        "https://github.com/karpathy/micrograd.git",
        "c911406e5ace8742e5841a7e0df113ecb5d54685",
        "tiny",
        "MIT",
        source_roots=(".",),
        test_roots=("test",),
        query_budgets=(2, 3, 4, 4, 4, 4, 5),
        read_budgets=(2, 3, 5, 5, 6, 6, 7),
    ),
    _spec(
        "nanoGPT",
        "sandbox/nanoGPT",
        "https://github.com/karpathy/nanoGPT.git",
        "3adf61e154c3fe3fca428ad6bc3818b27a3b8291",
        "tiny",
        "MIT",
        source_roots=(".",),
        test_roots=(),
        query_budgets=(3, 4, 5, 5, 6, 6, 7),
        read_budgets=(3, 4, 6, 6, 8, 8, 9),
    ),
    _spec(
        "click",
        "sandbox/click",
        "https://github.com/pallets/click.git",
        "b67832c2167e5b0ff6764a8c04a0a9087e697b5a",
        "small",
        "BSD-3-Clause",
        source_roots=("src", "tests"),
        test_roots=("tests",),
        query_budgets=(3, 4, 6, 6, 7, 7, 8),
        read_budgets=(3, 5, 7, 8, 9, 10, 11),
    ),
    _spec(
        "requests",
        "sandbox/requests",
        "https://github.com/psf/requests.git",
        "f361ead047be5cb873174218582f7d8b9fcd9f49",
        "small",
        "Apache-2.0",
        source_roots=("src", "tests"),
        test_roots=("tests",),
        query_budgets=(3, 5, 6, 7, 8, 8, 9),
        read_budgets=(4, 5, 8, 9, 10, 11, 12),
    ),
    _spec(
        "flask",
        "sandbox/flask",
        "https://github.com/pallets/flask.git",
        "36e4a824f340fdee7ed50937ba8e7f6bc7d17f81",
        "medium",
        "BSD-3-Clause",
        source_roots=("src", "tests"),
        test_roots=("tests",),
        query_budgets=(4, 6, 8, 9, 10, 11, 12),
        read_budgets=(5, 7, 10, 11, 13, 14, 15),
    ),
    _spec(
        "fastapi",
        "sandbox/fastapi",
        "https://github.com/fastapi/fastapi.git",
        "7cb06f360dd44efac059848df1a9beee7643b018",
        "medium",
        "MIT",
        source_roots=("fastapi", "tests"),
        test_roots=("tests",),
        query_budgets=(4, 6, 9, 10, 11, 12, 13),
        read_budgets=(5, 8, 11, 12, 14, 15, 16),
    ),
    _spec(
        "pytest",
        "sandbox/pytest",
        "https://github.com/pytest-dev/pytest.git",
        "efc013416e45d9162dc00dca9e4a83e1ef1c4089",
        "medium",
        "MIT",
        source_roots=("src", "testing"),
        test_roots=("testing",),
        query_budgets=(5, 7, 10, 11, 13, 14, 15),
        read_budgets=(6, 9, 13, 14, 16, 17, 18),
    ),
    _spec(
        "celery",
        "sandbox/celery",
        "https://github.com/celery/celery.git",
        "1432d9b6c6868a77e7ee2ede1650da00a8d187ac",
        "medium",
        "BSD-3-Clause",
        source_roots=("celery", "t"),
        test_roots=("t",),
        query_budgets=(5, 7, 10, 12, 14, 15, 17),
        read_budgets=(7, 10, 14, 15, 18, 19, 21),
    ),
    _spec(
        "django",
        "sandbox/django",
        "https://github.com/django/django.git",
        "65a9f14196c338d70889bd54753370606b3fb4eb",
        "large",
        "BSD-3-Clause",
        source_roots=("django", "tests"),
        test_roots=("tests",),
        query_budgets=(6, 9, 13, 15, 17, 19, 21),
        read_budgets=(8, 12, 17, 19, 22, 24, 26),
    ),
    _spec(
        "scikit-learn",
        "sandbox/scikit-learn",
        "https://github.com/scikit-learn/scikit-learn.git",
        "6b9e392862ac86f6a3f3b71ee89622d5af49bb4e",
        "large",
        "BSD-3-Clause",
        source_roots=("sklearn", "tests"),
        test_roots=("sklearn", "tests"),
        query_budgets=(6, 9, 14, 16, 18, 20, 22),
        read_budgets=(8, 12, 18, 20, 23, 25, 27),
    ),
    _spec(
        "pandas",
        "sandbox/pandas",
        "https://github.com/pandas-dev/pandas.git",
        "10b841796c8d9f750d572bf59da33348923a7183",
        "large",
        "BSD-3-Clause",
        source_roots=("pandas", "pandas/tests"),
        test_roots=("pandas/tests",),
        query_budgets=(7, 10, 15, 17, 20, 22, 24),
        read_budgets=(9, 13, 19, 22, 25, 28, 30),
    ),
    _spec(
        "transformers",
        "sandbox/transformers",
        "https://github.com/huggingface/transformers.git",
        "1f8daee0c80cd53a44b1094b79309dd8675d6392",
        "large",
        "Apache-2.0",
        source_roots=("src", "tests"),
        test_roots=("tests",),
        query_budgets=(8, 12, 17, 19, 22, 25, 28),
        read_budgets=(10, 15, 22, 25, 29, 32, 35),
    ),
)


def validate_sandbox_manifest(specs: Iterable[SandboxRepositorySpec]) -> None:
    values = tuple(specs)
    if len(values) < 12:
        raise ValueError("benchmark sandbox requires at least twelve repositories")
    if len({spec.path for spec in values}) != len(values):
        raise ValueError("sandbox repository paths must be unique")
    if len({spec.url for spec in values}) != len(values):
        raise ValueError("sandbox repository URLs must be unique")
    required_tiers = {"tiny", "small", "medium", "large"}
    if not required_tiers.issubset({spec.size_tier for spec in values}):
        raise ValueError("sandbox must cover tiny, small, medium, and large repositories")
    for spec in values:
        if not spec.path.startswith("sandbox/"):
            raise ValueError(f"sandbox path must be rooted under sandbox/: {spec.path!r}")
        if not spec.url.startswith("https://github.com/"):
            raise ValueError(f"sandbox source must be a public GitHub URL: {spec.url!r}")
        if re.fullmatch(r"[0-9a-f]{40}", spec.commit) is None:
            raise ValueError(f"sandbox repository {spec.name!r} is not pinned to a commit")
        if not spec.license_id:
            raise ValueError(f"sandbox repository {spec.name!r} is missing an open-source license")
        if not spec.source_globs or not spec.source_roots:
            raise ValueError(f"sandbox repository {spec.name!r} needs source search roots")
        for category in _CATEGORIES:
            if spec.query_budgets.get(category, 0) < 1:
                raise ValueError(f"sandbox repository {spec.name!r} lacks {category} query budget")
            if spec.read_budgets.get(category, 0) < 1:
                raise ValueError(f"sandbox repository {spec.name!r} lacks {category} read budget")


validate_sandbox_manifest(SANDBOX_REPOSITORIES)


__all__ = [
    "SANDBOX_REPOSITORIES",
    "SandboxRepositorySpec",
    "validate_sandbox_manifest",
]
