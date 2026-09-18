"""Idempotent artifact cleanup and orphan scanning."""

from __future__ import annotations

import time
from pathlib import Path

from .db import Database
from .storage import ArtifactStore, CleanupReport


def cleanup_expired(
    database: Database, store: ArtifactStore, now: float | None = None
) -> CleanupReport:
    now = time.time() if now is None else now
    with database.connection() as connection:
        rows = connection.execute(
            "SELECT id, path FROM artifacts WHERE expires_at <= ? OR published = 0",
            (now,),
        ).fetchall()
    report = store.delete_expired(Path(row["path"]) for row in rows)
    with database.connection() as connection:
        connection.execute(
            "DELETE FROM artifacts WHERE expires_at <= ? OR published = 0", (now,)
        )
    return report
