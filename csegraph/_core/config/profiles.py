from __future__ import annotations

import json
import os
from dataclasses import fields as dc_fields
from dataclasses import replace
from pathlib import Path
from typing import Optional, Union

from csegraph._core.core.models import ProfileConfig

AUTO_PROFILE = "auto"
SMALL_SOURCE_FILE_LIMIT = 500
LARGE_SOURCE_FILE_LIMIT = 5000

PROFILES = {
    "small": ProfileConfig(
        name="small",
        top_k=8,
        graph_radius=1,
        context_budget=16,
        raw_code_budget=3,
        semantic_threshold_relaxed=0.05,
    ),
    "medium": ProfileConfig(
        name="medium",
        top_k=20,
        graph_radius=2,
        context_budget=60,
        raw_code_budget=8,
        semantic_threshold_relaxed=0.03,
    ),
    "large": ProfileConfig(
        name="large",
        top_k=40,
        graph_radius=3,
        context_budget=120,
        raw_code_budget=12,
        semantic_threshold_relaxed=0.02,
    ),
}
PROFILE_CHOICES = (AUTO_PROFILE, *PROFILES.keys())


def get_profile(name: str) -> ProfileConfig:
    try:
        return PROFILES[name]
    except KeyError as exc:
        valid = ", ".join(sorted(PROFILES))
        raise ValueError(f"Unknown profile '{name}'. Expected one of: {valid}") from exc


def load_profile(
    name: Optional[str] = None,
    *,
    default: str = "medium",
    config_path: Optional[Union[str, Path]] = None,
    repo_root: Optional[Union[str, Path]] = None,
    source_file_count: Optional[int] = None,
) -> ProfileConfig:
    """Load a profile, optionally merging file-based overrides.

    Precedence:
    - name is not None -> explicit, used as base profile name.
    - name is None, config file has "profile" -> use config profile as base.
    - name is None, no config "profile" -> use *default*.

    Config file discovery (first match):
    1. Explicit *config_path*.
    2. <repo_root>/csegraph.json
    3. <repo_root>/csegraph.toml

    Raises ValueError on unknown config keys.
    Raises FileNotFoundError if explicit config_path doesn't exist.
    """
    path = _discover_config(config_path, repo_root)
    overrides = _load_config_file(path) if path is not None else {}

    config_profile = overrides.pop("profile", None)
    if name is not None:
        base_name = name
    elif config_profile is not None:
        base_name = config_profile
    else:
        base_name = default
    base = get_profile(
        resolve_profile_name(
            base_name,
            repo_root=repo_root,
            source_file_count=source_file_count,
        )
    )

    valid_fields = {f.name for f in dc_fields(ProfileConfig)}
    unknown = set(overrides) - valid_fields
    if unknown:
        raise ValueError(
            f"Unknown config keys: {sorted(unknown)}. Valid keys: {sorted(valid_fields)}"
        )

    if not overrides:
        return base
    return replace(base, **overrides)


def resolve_profile_name(
    name: Optional[str] = None,
    *,
    default: str = "medium",
    repo_root: Optional[Union[str, Path]] = None,
    source_file_count: Optional[int] = None,
) -> str:
    profile_name = name if name is not None else default
    if profile_name == AUTO_PROFILE:
        return _guess_profile_for_repo(repo_root, source_file_count=source_file_count)
    if profile_name not in PROFILES:
        valid = ", ".join(PROFILE_CHOICES)
        raise ValueError(f"Unknown profile '{profile_name}'. Expected one of: {valid}")
    return profile_name


def _discover_config(
    config_path: Optional[Union[str, Path]],
    repo_root: Optional[Union[str, Path]],
) -> Optional[Path]:
    if config_path is not None:
        p = Path(config_path)
        if not p.is_file():
            raise FileNotFoundError(f"Config file not found: {p}")
        return p
    if repo_root is not None:
        for fname in ("csegraph.json", "csegraph.toml"):
            p = Path(repo_root) / fname
            if p.is_file():
                return p
    return None


def _load_config_file(path: Path) -> dict:
    if path.suffix == ".toml":
        import tomlkit

        return dict(tomlkit.parse(path.read_text(encoding="utf-8")))
    return json.loads(path.read_text(encoding="utf-8"))


def _guess_profile_for_repo(
    repo_root: Optional[Union[str, Path]],
    *,
    source_file_count: Optional[int] = None,
) -> str:
    if source_file_count is not None:
        return _profile_name_for_source_file_count(source_file_count)
    if repo_root is None:
        return "medium"
    try:
        return _profile_name_for_source_file_count(_count_indexable_source_files(Path(repo_root)))
    except Exception:
        return "medium"


def _profile_name_for_source_file_count(count: int) -> str:
    if count < SMALL_SOURCE_FILE_LIMIT:
        return "small"
    if count < LARGE_SOURCE_FILE_LIMIT:
        return "medium"
    return "large"


def _count_indexable_source_files(repo_root: Path) -> int:
    from csegraph._core.discovery import iter_discoverable_rel_paths
    from csegraph._core.ignore import load_ignore_filter
    from csegraph._core.languages import registry
    from csegraph._core.languages.registry import UnsupportedLanguageError

    root = repo_root.resolve()
    if not root.is_dir():
        return SMALL_SOURCE_FILE_LIMIT

    ignore = load_ignore_filter(root)
    supported_extensions = registry.supported_extensions()
    count = 0
    for rel_path in iter_discoverable_rel_paths(root, ignore=ignore):
        ext = os.path.splitext(rel_path)[1]
        if ext not in supported_extensions:
            continue
        try:
            parser = registry.for_extension(ext)
        except UnsupportedLanguageError:
            continue
        if parser.excludes_rel_path(rel_path):
            continue
        path = root / rel_path
        if not path.is_file() or path.is_symlink():
            continue
        count += 1
        if count >= LARGE_SOURCE_FILE_LIMIT:
            return count
    return count
