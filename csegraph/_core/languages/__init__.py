from csegraph._core.languages.base import DefaultTokenizer
from csegraph._core.languages.documents import DocumentParser
from csegraph._core.languages.registry import registry
from csegraph._core.languages.treesitter.languages import available_language_factories
from csegraph._core.languages.treesitter.parser import TreeSitterParser

for _factory in available_language_factories():
    registry.register(TreeSitterParser(_factory()), DefaultTokenizer())

registry.register_explicit(DocumentParser(), DefaultTokenizer())
