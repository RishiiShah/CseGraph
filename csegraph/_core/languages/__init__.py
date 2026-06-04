from csegraph._core.languages.registry import registry
from csegraph._core.languages.base import DefaultTokenizer
from csegraph._core.languages.treesitter.languages import ALL_LANGUAGE_FACTORIES
from csegraph._core.languages.treesitter.parser import TreeSitterParser

for _factory in ALL_LANGUAGE_FACTORIES:
    registry.register(TreeSitterParser(_factory()), DefaultTokenizer())
