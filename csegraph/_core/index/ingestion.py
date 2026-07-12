"""Source discovery, parsing, caching, and include-root policy."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from csegraph._core.index.cache import ExtractionCache
from csegraph._core.languages.types import ParsedFile, ParsedSymbol


def _parse_with_cache(file_iter, repo_root: Path, cache: ExtractionCache) -> List[ParsedFile]:
    results: List[ParsedFile] = []
    for parser, path in file_iter:
        try:
            results.append(_parse_one_cached(parser, path, repo_root, cache))
        except ValueError:
            pass
    return results


def _parse_one_cached(parser, path: Path, repo_root: Path, cache: ExtractionCache) -> ParsedFile:
    from csegraph._core.languages.base import sha256_text

    resolved_path = path.resolve()
    resolved_root = Path(repo_root).resolve()
    if not resolved_path.is_relative_to(resolved_root):
        raise ValueError(
            f"Path '{path}' resolves to '{resolved_path}', which is outside repository root '{resolved_root}'"
        )
    source = resolved_path.read_text(encoding="utf-8")
    sha = sha256_text(source)
    rel = resolved_path.relative_to(resolved_root).as_posix()

    cached = cache.get(rel, sha)
    if cached is not None:
        return cached

    parsed = parser.parse(path, repo_root)
    cache.put(parsed)
    return parsed


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)


def _file_summary(parsed: ParsedFile) -> str:
    names = ", ".join(symbol.name for symbol in parsed.symbols[:8]) or "no symbols"
    return f"Module {parsed.rel_path} defines {names}."


def _symbol_summary(symbol: ParsedSymbol) -> str:
    parts = [symbol.signature or f"{symbol.kind} {symbol.name}"]
    if symbol.docstring:
        parts.append(symbol.docstring.split(".")[0].replace("\n", " ").strip())
    if symbol.bases:
        parts.append("inherits " + ", ".join(symbol.bases[:4]))
    if symbol.calls:
        parts.append("calls " + ", ".join(symbol.calls[:8]))
    return " - ".join(part for part in parts if part)


def _normalize_include_roots(
    repo_root: Path,
    include_roots: Optional[Sequence[str | Path]],
) -> tuple[str, ...]:
    if not include_roots:
        return ()

    prefixes: List[str] = []
    for raw_root in include_roots:
        raw_path = Path(raw_root)
        if raw_path.is_absolute():
            resolved = raw_path.resolve()
            try:
                rel_path = resolved.relative_to(repo_root).as_posix()
            except ValueError as exc:
                raise ValueError(
                    f"Include root '{raw_root}' is outside repository root '{repo_root}'."
                ) from exc
        else:
            rel_path = raw_path.as_posix()
        rel_path = rel_path.replace("\\", "/").strip("/")
        if rel_path in ("", "."):
            continue
        if ".." in Path(rel_path).parts:
            raise ValueError(f"Include root '{raw_root}' must stay inside the repository.")
        if rel_path not in prefixes:
            prefixes.append(rel_path)
    return tuple(prefixes)


def _include_roots_from_metadata(metadata: Dict[str, str]) -> tuple[str, ...]:
    raw_value = metadata.get("include_roots")
    if not raw_value:
        return ()
    try:
        values = json.loads(raw_value)
    except json.JSONDecodeError:
        return ()
    if not isinstance(values, list):
        return ()
    return tuple(str(value).strip("/") for value in values if str(value).strip("/"))


def _filter_included_files(
    file_iter: Iterable[tuple],
    repo_root: Path,
    include_roots: Sequence[str],
) -> Iterable[tuple]:
    if not include_roots:
        yield from file_iter
        return
    for parser, path in file_iter:
        rel_path = path.resolve().relative_to(repo_root).as_posix()
        if _is_included_rel_path(rel_path, include_roots):
            yield parser, path


def _is_included_rel_path(rel_path: str, include_roots: Sequence[str]) -> bool:
    if not include_roots:
        return True
    normalized = rel_path.replace("\\", "/").strip("/")
    return any(normalized == root or normalized.startswith(f"{root}/") for root in include_roots)
