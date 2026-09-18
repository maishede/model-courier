"""Resource and task quota checks for the low-cost control plane."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .db import Database

MAX_DEVICE_ACTIVE = 10
MAX_INSTANCE_ACTIVE = 100
MAX_ARTIFACT_BYTES = 10 * 1024**3
MIN_FREE_BYTES = 5 * 1024**3


class QuotaExceededError(ValueError):
    pass


@dataclass(frozen=True)
class Reservation:
    owner_id: str
    device_id: str
    bytes_reserved: int


class QuotaManager:
    def __init__(self, database: Database, artifact_root: Path) -> None:
        self.database = database
        self.artifact_root = Path(artifact_root)

    def reserve(self, owner_id: str, device_id: str, bytes_reserved: int) -> Reservation:
        if bytes_reserved < 0:
            raise QuotaExceededError("reserved size cannot be negative")
        free_bytes = shutil.disk_usage(self.artifact_root).free
        if free_bytes < MIN_FREE_BYTES or bytes_reserved > free_bytes - MIN_FREE_BYTES:
            raise QuotaExceededError("artifact disk reserve is exhausted")
        with self.database.connection() as connection:
            active_device = connection.execute(
                """
                SELECT COUNT(*) AS count FROM tasks
                WHERE owner_id = ? AND device_id = ?
                  AND status NOT IN ('succeeded', 'failed', 'expired', 'canceled')
                """,
                (owner_id, device_id),
            ).fetchone()["count"]
            active_instance = connection.execute(
                """
                SELECT COUNT(*) AS count FROM tasks
                WHERE status NOT IN ('succeeded', 'failed', 'expired', 'canceled')
                """
            ).fetchone()["count"]
            used_bytes = connection.execute(
                "SELECT COALESCE(SUM(size_bytes), 0) AS total FROM artifacts"
            ).fetchone()["total"]
        if active_device >= MAX_DEVICE_ACTIVE:
            raise QuotaExceededError("device active-task quota is exhausted")
        if active_instance >= MAX_INSTANCE_ACTIVE:
            raise QuotaExceededError("instance active-task quota is exhausted")
        if used_bytes + bytes_reserved > MAX_ARTIFACT_BYTES:
            raise QuotaExceededError("artifact budget is exhausted")
        return Reservation(owner_id, device_id, bytes_reserved)
