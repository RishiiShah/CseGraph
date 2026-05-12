from csegraph_core.languages.registry import registry
from csegraph_core.languages.python.parser import PythonParser
from csegraph_core.languages.python.tokenizer import PythonTokenizer

registry.register(PythonParser(), PythonTokenizer())

try:
    from csegraph_core.languages.treesitter.languages import ALL_LANGUAGE_FACTORIES
    from csegraph_core.languages.treesitter.parser import TreeSitterParser
    from csegraph_core.languages.treesitter.tokenizer import TreeSitterTokenizer

    for _factory in ALL_LANGUAGE_FACTORIES:
        try:
            _config = _factory()
            registry.register(TreeSitterParser(_config), TreeSitterTokenizer())
        except ImportError:
            pass
except ImportError:
    pass
