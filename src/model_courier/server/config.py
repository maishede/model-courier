"""Runtime configuration for the control plane."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ServerConfig:
    database_path: Path
    artifact_root: Path

    @classmethod
    def from_root(cls, root: Path) -> ServerConfig:
        root = Path(root)
        return cls(database_path=root / "control.sqlite3", artifact_root=root / "artifacts")
