"""Schema migration registry.

To add a new migration, create `mNNNN_v{N}_to_v{N+1}.py` exporting
`FROM_VERSION`, `TO_VERSION`, and `def upgrade(conn)`, then register it
in MIGRATIONS below.
"""
from __future__ import annotations

import sqlite3
from typing import Callable, List, NamedTuple

from csegraph_core.index.migrations import m0001_v1_to_v2, m0002_v2_to_v3


class Migration(NamedTuple):
    from_version: str
    to_version: str
    upgrade: Callable[[sqlite3.Connection], None]


MIGRATIONS: List[Migration] = [
    Migration(m0001_v1_to_v2.FROM_VERSION, m0001_v1_to_v2.TO_VERSION, m0001_v1_to_v2.upgrade),
    Migration(m0002_v2_to_v3.FROM_VERSION, m0002_v2_to_v3.TO_VERSION, m0002_v2_to_v3.upgrade),
]


def apply_pending(conn: sqlite3.Connection, current_version: str, target_version: str) -> str:
    """Walk the registry, applying migrations whose FROM_VERSION matches the current.

    Each migration's upgrade() commits its own transaction. After each upgrade,
    schema_meta.schema_version is bumped. Returns the final stored version.
    """
    version = current_version
    while version != target_version:
        next_step = next((m for m in MIGRATIONS if m.from_version == version), None)
        if next_step is None:
            raise RuntimeError(
                f"No migration registered from {version!r} (target {target_version!r})."
            )
        next_step.upgrade(conn)
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (next_step.to_version,),
        )
        conn.commit()
        version = next_step.to_version
    return version
