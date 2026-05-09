"""Query-side tokenizer.

Currently identical to the Python source tokenizer; held separately so
query-side and source-side can diverge in future without touching call sites.
"""
from __future__ import annotations

from typing import List

from csegraph_core.text.tokens import _default_text_tokenize


class QueryTokenizer:
    def tokenize(self, text: str) -> List[str]:
        return _default_text_tokenize(text)


query_tokenizer = QueryTokenizer()
