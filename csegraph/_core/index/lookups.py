"""Lazy repository lookups and write-batch state for index services."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TypeVar, overload

from csegraph._core.index.repository import ProjectIndex

_DefaultT = TypeVar("_DefaultT")


class _LazySymbolLookup(dict[str, List[str]]):
    def __init__(
        self,
        index: ProjectIndex,
        node_to_file_node: Dict[str, str],
        node_kind_by_id: Dict[str, str],
    ) -> None:
        super().__init__()
        self.index = index
        self.node_to_file_node = node_to_file_node
        self.node_kind_by_id = node_kind_by_id

    def _load(self, lookup_name: str) -> List[str]:
        if dict.__contains__(self, lookup_name):
            return dict.__getitem__(self, lookup_name)
        rows = self.index.conn.execute(
            """
            SELECT lookup.symbol_id, symbol.file_id, symbol.kind
            FROM symbol_lookup AS lookup
            JOIN symbols AS symbol ON symbol.id = lookup.symbol_id
            WHERE lookup.lookup_name = ?
            ORDER BY lookup.symbol_id
            """,
            (lookup_name,),
        ).fetchall()
        candidates = [str(row["symbol_id"]) for row in rows]
        for row in rows:
            node_id = str(row["symbol_id"])
            self.node_to_file_node[node_id] = str(row["file_id"])
            self.node_kind_by_id[node_id] = str(row["kind"])
        dict.__setitem__(self, lookup_name, candidates)
        return candidates

    def __getitem__(self, lookup_name: str) -> List[str]:
        candidates = self._load(lookup_name)
        if not candidates:
            raise KeyError(lookup_name)
        return candidates

    def __contains__(self, lookup_name: object) -> bool:
        if not isinstance(lookup_name, str):
            return False
        return bool(self._load(lookup_name))

    def add_candidate(self, lookup_name: str, symbol_id: str) -> None:
        if dict.__contains__(self, lookup_name):
            dict.__getitem__(self, lookup_name).append(symbol_id)

    @overload
    def get(self, lookup_name: str, default: None = None, /) -> Optional[List[str]]: ...

    @overload
    def get(self, lookup_name: str, default: List[str], /) -> List[str]: ...

    @overload
    def get(self, lookup_name: str, default: _DefaultT, /) -> List[str] | _DefaultT: ...

    def get(
        self,
        lookup_name: str,
        default: Any = None,
        /,
    ) -> Any:
        candidates = self._load(lookup_name)
        if candidates:
            return candidates
        return default


class _LazyModuleLookup(dict[str, str]):
    def __init__(self, index: ProjectIndex) -> None:
        super().__init__()
        self.index = index
        self.loaded: set[str] = set()

    def _load(self, module_name: str) -> Optional[str]:
        if module_name in self.loaded:
            return dict.get(self, module_name)
        row = self.index.conn.execute(
            """
            SELECT module_lookup.file_id
            FROM module_lookup
            JOIN files ON files.id = module_lookup.file_id
            WHERE module_name = ?
            ORDER BY files.path DESC
            LIMIT 1
            """,
            (module_name,),
        ).fetchone()
        self.loaded.add(module_name)
        if row is None:
            return None
        file_id = str(row["file_id"])
        dict.__setitem__(self, module_name, file_id)
        return file_id

    def __contains__(self, module_name: object) -> bool:
        if not isinstance(module_name, str):
            return False
        return self._load(module_name) is not None

    def __getitem__(self, module_name: str) -> str:
        file_id = self._load(module_name)
        if file_id is None:
            raise KeyError(module_name)
        return file_id

    @overload
    def get(self, module_name: str, default: None = None, /) -> Optional[str]: ...

    @overload
    def get(self, module_name: str, default: str, /) -> str: ...

    @overload
    def get(self, module_name: str, default: _DefaultT, /) -> str | _DefaultT: ...

    def get(self, module_name: str, default: Any = None, /) -> Any:
        return self._load(module_name) or default


__all__ = [
    "_LazyModuleLookup",
    "_LazySymbolLookup",
]
