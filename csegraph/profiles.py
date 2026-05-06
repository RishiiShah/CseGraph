from __future__ import annotations

from csegraph.models import ProfileConfig


PROFILES = {
    "small": ProfileConfig(
        name="small",
        top_k=8,
        graph_radius=1,
        context_budget=20,
        import_budget=8,
        raw_code_budget=3,
    ),
    "medium": ProfileConfig(
        name="medium",
        top_k=20,
        graph_radius=2,
        context_budget=60,
        import_budget=20,
        raw_code_budget=8,
    ),
    "large": ProfileConfig(
        name="large",
        top_k=40,
        graph_radius=3,
        context_budget=120,
        import_budget=35,
        raw_code_budget=12,
    ),
}


def get_profile(name: str) -> ProfileConfig:
    try:
        return PROFILES[name]
    except KeyError as exc:
        valid = ", ".join(sorted(PROFILES))
        raise ValueError(f"Unknown profile '{name}'. Expected one of: {valid}") from exc
