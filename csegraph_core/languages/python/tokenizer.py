from __future__ import annotations

from typing import List

from csegraph_core.text.tokens import _default_text_tokenize


class PythonTokenizer:
    def tokenize(self, text: str) -> List[str]:
        return _default_text_tokenize(text)


def code_tokenize(text: str) -> List[str]:
    return _default_text_tokenize(text)
