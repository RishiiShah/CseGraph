from __future__ import annotations

from typing import Iterable, Set

from csegraph_core.text.query_tokenizer import query_tokenizer


def extract_query_entities(query_text: str, known_names: Iterable[str]) -> Set[str]:
    tokens = set(query_tokenizer.tokenize(query_text))
    known_lower = {name.lower(): name for name in known_names}
    entities: Set[str] = set()
    for token in tokens:
        if token in known_lower:
            entities.add(known_lower[token])
    query_lower = query_text.lower()
    q_len = len(query_lower)
    for name in known_names:
        if len(name) > q_len:
            continue
        lowered = name.lower()
        if lowered in query_lower:
            entities.add(name)
    return entities
