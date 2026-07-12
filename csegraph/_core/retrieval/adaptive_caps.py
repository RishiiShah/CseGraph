from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from csegraph._core.core.models import ContextRequest
from csegraph._core.text.query_tokenizer import query_tokenizer

HARD_MAX_CANDIDATES = 256
HARD_MAX_SLICES = 16
HARD_MAX_RELATIONSHIPS = 128

_CONTEXT_TERMS = {
    "architecture",
    "caller",
    "callers",
    "dependency",
    "dependencies",
    "flow",
    "impact",
    "integration",
    "path",
    "references",
    "routing",
    "test",
    "tests",
    "usage",
}


@dataclass(frozen=True)
class RetrievalCaps:
    candidate_limit: int
    slice_limit: int
    relationship_limit: int
    hard_candidate_limit: int
    hard_slice_limit: int

    def as_dict(self) -> dict[str, int]:
        return {
            "candidate_limit": self.candidate_limit,
            "slice_limit": self.slice_limit,
            "relationship_limit": self.relationship_limit,
            "hard_candidate_limit": self.hard_candidate_limit,
            "hard_slice_limit": self.hard_slice_limit,
        }


def derive_retrieval_caps(
    request: ContextRequest,
    *,
    intent: str,
    plan_mode: str,
    metadata: Mapping[str, str],
    exact_target: bool,
) -> RetrievalCaps:
    file_count = _metadata_int(metadata, "file_count")
    symbol_count = _metadata_int(metadata, "symbol_count")
    repository_band = _repository_band(file_count, symbol_count)
    tokens = set(query_tokenizer.tokenize(request.task))
    context_term_count = len(tokens & _CONTEXT_TERMS)
    budget_room = max(1, min(4, request.token_budget // 800))

    if plan_mode == "structural":
        slice_base = 4
        relationship_base = 40
        complexity_bonus = 2
    elif intent in {"debug", "review", "edit", "test-impact"} or plan_mode == "impact":
        slice_base = 3
        relationship_base = 24
        complexity_bonus = 1
    else:
        slice_base = 1
        relationship_base = 8
        complexity_bonus = 0

    if context_term_count >= 2:
        slice_base += min(2, context_term_count - 1)
        relationship_base += 8
    elif context_term_count == 1 and plan_mode != "lexical":
        relationship_base += 4
    if "dependencies" in tokens and plan_mode == "impact":
        slice_base += 1
    if {"callers", "dependencies"}.issubset(tokens) and plan_mode == "impact":
        slice_base += 1
    if intent == "debug" and plan_mode == "impact":
        slice_base = max(slice_base, 5)

    slice_limit = min(
        HARD_MAX_SLICES,
        max(slice_base, budget_room + complexity_bonus),
    )
    if exact_target and plan_mode == "lexical":
        slice_limit = 1

    candidate_base = 16 if exact_target else 32
    candidate_base += repository_band * 24
    candidate_base += context_term_count * 8
    candidate_base += complexity_bonus * 16
    candidate_base += max(0, budget_room - 1) * 8
    if plan_mode == "structural":
        candidate_base += 24
    candidate_limit = min(HARD_MAX_CANDIDATES, max(1, candidate_base))

    relationship_limit = min(
        HARD_MAX_RELATIONSHIPS,
        max(
            1,
            relationship_base + repository_band * 16 + max(0, budget_room - 1) * 8,
        ),
    )
    return RetrievalCaps(
        candidate_limit=candidate_limit,
        slice_limit=slice_limit,
        relationship_limit=relationship_limit,
        hard_candidate_limit=HARD_MAX_CANDIDATES,
        hard_slice_limit=HARD_MAX_SLICES,
    )


def _metadata_int(metadata: Mapping[str, str], key: str) -> int:
    try:
        return max(0, int(metadata.get(key, "0")))
    except (TypeError, ValueError):
        return 0


def _repository_band(file_count: int, symbol_count: int) -> int:
    if file_count <= 100 and symbol_count <= 1_000:
        return 0
    if file_count <= 1_000 and symbol_count <= 20_000:
        return 1
    return 2


__all__ = [
    "HARD_MAX_CANDIDATES",
    "HARD_MAX_RELATIONSHIPS",
    "HARD_MAX_SLICES",
    "RetrievalCaps",
    "derive_retrieval_caps",
]
