"""SQLite persistence for non-secret local Agent profiles."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .models import ModelBinding


class AgentStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS model_bindings (
                    binding_id TEXT PRIMARY KEY,
                    binding_json TEXT NOT NULL,
                    verified INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS agent_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    accepting INTEGER NOT NULL DEFAULT 0
                );
                INSERT OR IGNORE INTO agent_state (id, accepting) VALUES (1, 0);
                """
            )

    def save_binding(self, binding: ModelBinding) -> None:
        payload = binding.model_dump(mode="json")
        _reject_secrets(payload)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO model_bindings (binding_id, binding_json, verified)
                VALUES (?, ?, 0)
                ON CONFLICT(binding_id) DO UPDATE SET
                    binding_json = excluded.binding_json,
                    verified = 0
                """,
                (binding.binding_id, encoded),
            )

    def list_bindings(self) -> list[ModelBinding]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT binding_json FROM model_bindings ORDER BY binding_id"
            ).fetchall()
        return [ModelBinding.model_validate(json.loads(row["binding_json"])) for row in rows]

    def get_binding(self, binding_id: str) -> ModelBinding | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT binding_json FROM model_bindings WHERE binding_id = ?", (binding_id,)
            ).fetchone()
        return None if row is None else ModelBinding.model_validate(json.loads(row["binding_json"]))

    def mark_verified(self, binding_id: str, verified: bool = True) -> None:
        with self.connection() as connection:
            connection.execute(
                "UPDATE model_bindings SET verified = ? WHERE binding_id = ?",
                (int(verified), binding_id),
            )

    def has_verified_enabled_binding(self) -> bool:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM model_bindings "
                "WHERE verified = 1 AND json_extract(binding_json, '$.enabled') = 1 LIMIT 1"
            ).fetchone()
        return row is not None

    def set_accepting(self, accepting: bool) -> None:
        with self.connection() as connection:
            connection.execute(
                "UPDATE agent_state SET accepting = ? WHERE id = 1", (int(accepting),)
            )

    def accepting(self) -> bool:
        with self.connection() as connection:
            row = connection.execute("SELECT accepting FROM agent_state WHERE id = 1").fetchone()
        return bool(row["accepting"]) if row else False

    def raw_binding_json(self, binding_id: str) -> str:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT binding_json FROM model_bindings WHERE binding_id = ?", (binding_id,)
            ).fetchone()
        if row is None:
            raise KeyError(binding_id)
        return row["binding_json"]


def _reject_secrets(value: object, path: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if any(
                word in key.lower()
                for word in ("token", "secret", "password", "authorization")
            ):
                raise ValueError(f"secret value must use a secret reference: {path}{key}")
            _reject_secrets(child, f"{path}{key}.")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secrets(child, f"{path}{index}.")
