"""Import binding and call/edge resolution helpers for indexing."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from hashlib import blake2b
from typing import Dict, List, Mapping, Optional, Sequence

from csegraph._core.index.lookups import _LazyModuleLookup
from csegraph._core.index.repository import ProjectIndex
from csegraph._core.languages.types import ParsedFile, ParsedImport, ParsedReference, ParsedSymbol

_EXTRACTED = "EXTRACTED"


@dataclass(frozen=True)
class _ImportBinding:
    import_name: str
    local_name: str
    imported_name: str
    qualified_name: Optional[str]
    binding_kind: str
    resolved_file_id: Optional[str]
    resolved_symbol_id: Optional[str]
    resolution_status: str
    start_line: int
    end_line: int
    source: str


@dataclass(frozen=True)
class _TargetResolution:
    target: Optional[str]
    status: str
    strategy: str
    candidates: tuple[str, ...] = ()


def _normalized_import_records(parsed: ParsedFile) -> List[ParsedImport]:
    if parsed.import_records:
        return parsed.import_records
    return [
        ParsedImport(
            name=name,
            start_line=1,
            end_line=1,
            source="",
        )
        for name in parsed.imports
    ]


def _import_bindings_for(
    import_record: ParsedImport,
    resolved_file_id: Optional[str],
    symbol_by_name: Mapping[str, Sequence[str]],
    node_to_file_node: Mapping[str, str],
) -> List[_ImportBinding]:
    metadata = import_record.metadata
    raw_bindings = metadata.get("imports")
    binding_specs: List[Dict[str, object]] = []
    if isinstance(raw_bindings, list):
        binding_specs.extend(item for item in raw_bindings if isinstance(item, dict))

    if not binding_specs:
        imported_name = str(
            metadata.get("imported_name") or _symbol_name_from_import_target(import_record.name)
        )
        local_name = str(metadata.get("local_name") or imported_name)
        binding_specs.append(
            {
                "name": imported_name,
                "local": local_name,
                "qualified": metadata.get("module") or import_record.name,
            }
        )

    bindings: List[_ImportBinding] = []
    for spec in binding_specs:
        imported_name = str(spec.get("name") or metadata.get("imported_name") or "")
        local_name = str(spec.get("local") or imported_name)
        qualified_name = spec.get("qualified")
        if not isinstance(qualified_name, str):
            module = metadata.get("module")
            if isinstance(module, str) and imported_name not in {"", "*", "default"}:
                qualified_name = f"{module}.{imported_name}"
            elif isinstance(module, str):
                qualified_name = module
            else:
                qualified_name = None

        if imported_name == "*" or metadata.get("namespace") == local_name:
            binding_kind = "namespace"
        elif imported_name == "default":
            binding_kind = "default"
        elif metadata.get("style") in {"import", "require"}:
            binding_kind = "module"
        else:
            binding_kind = "named"

        candidates = _candidates_in_file(
            imported_name,
            resolved_file_id,
            symbol_by_name,
            node_to_file_node,
        )
        if len(candidates) == 1:
            resolved_symbol_id = candidates[0]
            status = "resolved"
        elif len(candidates) > 1:
            resolved_symbol_id = None
            status = "ambiguous"
        elif resolved_file_id:
            resolved_symbol_id = None
            status = "file_resolved"
        else:
            resolved_symbol_id = None
            status = "external"

        bindings.append(
            _ImportBinding(
                import_name=import_record.name,
                local_name=local_name,
                imported_name=imported_name,
                qualified_name=qualified_name,
                binding_kind=binding_kind,
                resolved_file_id=resolved_file_id,
                resolved_symbol_id=resolved_symbol_id,
                resolution_status=status,
                start_line=import_record.start_line,
                end_line=import_record.end_line,
                source=import_record.source,
            )
        )
    return bindings


def _symbol_name_from_import_target(target: str) -> str:
    return target.rsplit(".", 1)[-1].rsplit("/", 1)[-1]


def _references_for(
    symbol: ParsedSymbol,
    kind: str,
    name: str,
) -> List[ParsedReference]:
    matches = [
        reference
        for reference in symbol.references
        if reference.kind == kind and reference.name == name
    ]
    if matches:
        return matches
    return [
        ParsedReference(
            kind=kind,
            name=name,
            start_line=symbol.start_line,
            end_line=symbol.end_line,
            source="",
        )
    ]


def _test_references_for(symbol: ParsedSymbol, name: str) -> List[ParsedReference]:
    matches = [
        reference
        for reference in symbol.references
        if reference.kind in {"test", "calls"} and reference.name == name
    ]
    if matches:
        return matches
    return [
        ParsedReference(
            kind="test",
            name=name,
            start_line=symbol.start_line,
            end_line=symbol.end_line,
            source="",
        )
    ]


def _edge_occurrence(
    source: str,
    target: Optional[str],
    relation: str,
    source_file_id: str,
    enclosing_symbol_id: Optional[str],
    name: str,
    start_line: int,
    end_line: int,
    source_text: str,
    resolution: _TargetResolution,
) -> tuple:
    return (
        source,
        target,
        relation,
        source_file_id,
        enclosing_symbol_id,
        name,
        start_line,
        end_line,
        source_text,
        resolution.status,
        resolution.strategy,
        json.dumps(list(resolution.candidates), sort_keys=True),
    )


def _with_occurrence_keys(rows: Sequence[tuple]) -> List[tuple]:
    ordinals: Dict[tuple, int] = defaultdict(int)
    keyed: List[tuple] = []
    for row in rows:
        location = (row[3], row[4], row[2], row[5], row[6], row[7], row[8])
        ordinal = ordinals[location]
        ordinals[location] += 1
        raw = "\x1f".join("" if value is None else str(value) for value in (*location, ordinal))
        key = blake2b(raw.encode("utf-8"), digest_size=16).digest()
        keyed.append((key, *row))
    return keyed


def _module_to_file_id(index: ProjectIndex) -> Dict[str, str]:
    return _LazyModuleLookup(index)


def _pick_call_target(
    symbol: str,
    current_file_id: str,
    symbol_by_name: Dict[str, List[str]],
    node_to_file_node: Dict[str, str],
    node_kind_by_id: Dict[str, str],
    preferred_file_ids: Sequence[str] | None = None,
) -> Optional[str]:
    del node_kind_by_id
    candidates = sorted(set(symbol_by_name.get(symbol, [])))
    local = [node_id for node_id in candidates if node_to_file_node.get(node_id) == current_file_id]
    if len(local) == 1:
        return local[0]

    preferred = set(preferred_file_ids or ())
    explicitly_imported = [
        node_id for node_id in candidates if node_to_file_node.get(node_id) in preferred
    ]
    if len(explicitly_imported) == 1:
        return explicitly_imported[0]
    return None


def _resolve_call_target(
    symbol: str,
    current_file_id: str,
    symbol_by_name: Dict[str, List[str]],
    node_to_file_node: Dict[str, str],
    node_kind_by_id: Dict[str, str],
    bindings_by_local: Dict[str, List[_ImportBinding]],
) -> _TargetResolution:
    del node_kind_by_id
    candidates = sorted(set(symbol_by_name.get(symbol, [])))
    local = tuple(
        node_id for node_id in candidates if node_to_file_node.get(node_id) == current_file_id
    )
    if len(local) == 1:
        return _TargetResolution(local[0], "resolved", "same_file", local)
    if len(local) > 1:
        return _TargetResolution(None, "ambiguous", "same_file", local)

    bound_candidates: List[str] = []
    for binding in bindings_by_local.get(symbol, []):
        if binding.resolved_symbol_id:
            bound_candidates.append(binding.resolved_symbol_id)
            continue
        bound_candidates.extend(
            _candidates_in_file(
                binding.imported_name,
                binding.resolved_file_id,
                symbol_by_name,
                node_to_file_node,
            )
        )
    bound = tuple(sorted(set(bound_candidates)))
    if len(bound) == 1:
        return _TargetResolution(bound[0], "resolved", "explicit_import", bound)
    if len(bound) > 1:
        return _TargetResolution(None, "ambiguous", "explicit_import", bound)

    status = "ambiguous" if len(candidates) > 1 else "unresolved"
    return _TargetResolution(None, status, "unbound_name", tuple(candidates))


def _candidates_in_file(
    symbol: str,
    file_id: Optional[str],
    symbol_by_name: Mapping[str, Sequence[str]],
    node_to_file_node: Mapping[str, str],
) -> List[str]:
    if not file_id or symbol in {"", "*", "default"}:
        return []
    return sorted(
        {
            node_id
            for node_id in symbol_by_name.get(symbol, [])
            if node_to_file_node.get(node_id) == file_id
        }
    )


def _edge(
    source: str,
    target: str,
    relation: str,
    confidence: float = 1.0,
    confidence_tier: str = _EXTRACTED,
) -> tuple:
    return (
        source,
        target,
        relation,
        confidence,
        confidence_tier,
    )
