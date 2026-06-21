from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, FrozenSet, Optional, Tuple


@dataclass
class LanguageConfig:
    name: str
    extensions: Tuple[str, ...]
    lang_map: Dict[str, Any]

    class_types: FrozenSet[str]
    function_types: FrozenSet[str]
    call_types: FrozenSet[str] = frozenset({"call_expression"})
    function_boundary_types: FrozenSet[str] = frozenset()

    class_body_field: str = "body"
    class_body_type: str = ""
    class_name_field: str = "name"

    declaration_wrapper_types: FrozenSet[str] = frozenset()
    decorator_wrapper_type: str = ""  # e.g. "decorated_definition" for Python

    impl_types: FrozenSet[str] = frozenset()
    impl_type_field: str = "type"
    impl_body_field: str = "body"

    method_receiver_field: str = ""

    export_type: str = ""

    lambda_decl_types: FrozenSet[str] = frozenset()
    lambda_value_type: str = ""

    heritage_type: str = ""
    heritage_clause_types: FrozenSet[str] = frozenset()
    heritage_ident_types: FrozenSet[str] = frozenset({"identifier", "type_identifier"})
    superclass_field: str = ""
    interfaces_field: str = ""

    extra_excluded_dirs: FrozenSet[str] = frozenset()

    test_dir_prefixes: Tuple[str, ...] = ("tests/", "test/")
    test_file_suffixes: Tuple[str, ...] = ()
    test_name_prefixes: Tuple[str, ...] = ("test",)

    index_filenames: Tuple[str, ...] = ()

    extract_imports_fn: Optional[Callable] = None
    extract_doc_fn: Optional[Callable] = None
    module_name_fn: Optional[Callable] = None
    resolve_import_fn: Optional[Callable] = None
