from pathlib import Path
from typing import Dict, Optional

import pytest

from csegraph import (
    BaseParser,
    DefaultTokenizer,
    LanguageConfig,
    ParsedFile,
    register_parser,
    register_tree_sitter_language,
)


class PluginParser(BaseParser):
    language = "plugin_test"
    extensions = (".plugtest",)

    def parse(self, path: Path, root_dir: Path) -> ParsedFile:
        rel_path = path.resolve().relative_to(root_dir.resolve()).as_posix()
        return ParsedFile(
            rel_path=rel_path,
            abs_path=str(path.resolve()),
            sha256="custom",
            mtime=path.stat().st_mtime,
            size=path.stat().st_size,
            language=self.language,
        )

    def module_name_from_relpath(self, rel_path: str) -> Optional[str]:
        return rel_path.rsplit(".", 1)[0]

    def resolve_local_import(
        self,
        import_name: str,
        module_to_file_id: Dict[str, str],
        current_module: Optional[str],
    ) -> Optional[str]:
        return module_to_file_id.get(import_name)


class MissingLanguageParser(PluginParser):
    language = ""
    extensions = (".missinglang",)


def test_register_parser_public_api_registers_custom_parser():
    from csegraph._core.languages import registry

    parser = PluginParser()

    registered = register_parser(parser)

    assert registered is parser
    assert registry.for_extension(".plugtest") is parser
    assert isinstance(registry.tokenizer_for("plugin_test"), DefaultTokenizer)


def test_register_parser_validates_language_name():
    with pytest.raises(ValueError, match="language"):
        register_parser(MissingLanguageParser())


def test_register_tree_sitter_language_public_api_registers_config():
    from csegraph._core.languages import registry

    config = LanguageConfig(
        name="tree_sitter_plugin_test",
        extensions=(".tsplug",),
        lang_map={},
        class_types=frozenset(),
        function_types=frozenset(),
    )

    parser = register_tree_sitter_language(config)

    assert parser.language == "tree_sitter_plugin_test"
    assert registry.for_extension(".tsplug") is parser
