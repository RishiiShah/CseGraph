from __future__ import annotations

import json
from dataclasses import fields as dc_fields, replace
from pathlib import Path
from typing import Optional, Union

from csegraph._core.core.models import ProfileConfig


PROFILES = {
    "small": ProfileConfig(
        name="small",
        top_k=8,
        graph_radius=1,
        context_budget=20,
        raw_code_budget=3,
    ),
    "medium": ProfileConfig(
        name="medium",
        top_k=20,
        graph_radius=2,
        context_budget=60,
        raw_code_budget=8,
    ),
    "large": ProfileConfig(
        name="large",
        top_k=40,
        graph_radius=3,
        context_budget=120,
        raw_code_budget=12,
    ),
}


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
    base = get_profile(base_name)

    valid_fields = {f.name for f in dc_fields(ProfileConfig)}
    unknown = set(overrides) - valid_fields
    if unknown:
        raise ValueError(
            f"Unknown config keys: {sorted(unknown)}. "
            f"Valid keys: {sorted(valid_fields)}"
        )

    if not overrides:
        return base
    return replace(base, **overrides)


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
        try:
            import tomllib
        except ImportError as exc:
            raise ImportError(
                "csegraph.toml requires Python 3.11+ (tomllib). "
                "Use csegraph.json instead or upgrade Python."
            ) from exc
        return tomllib.loads(path.read_text(encoding="utf-8"))
    return json.loads(path.read_text(encoding="utf-8"))
