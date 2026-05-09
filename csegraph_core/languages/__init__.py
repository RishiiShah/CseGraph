from csegraph_core.languages.registry import registry
from csegraph_core.languages.python.parser import PythonParser
from csegraph_core.languages.python.tokenizer import PythonTokenizer

registry.register(PythonParser(), PythonTokenizer())
