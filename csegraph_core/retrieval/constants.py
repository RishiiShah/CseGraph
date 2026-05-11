from __future__ import annotations

REASON_ORDER = (
    "target",
    "direct_call",
    "caller",
    "import_dependency",
    "same_file",
    "parent_class",
    "small_helper",
    "test_related",
    "raw_code_fallback",
    "embedding_match",
    "lexical_match",
    "graph_neighbor",
)

VALID_REASONS = frozenset(REASON_ORDER)
