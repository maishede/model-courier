"""SQLite persistence for non-secret local Agent profiles."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from model_courier.contracts import canonical_json_digest

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
                    verified INTEGER NOT NULL DEFAULT 0,
                    verified_digest TEXT
                );
                CREATE TABLE IF NOT EXISTS agent_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    accepting INTEGER NOT NULL DEFAULT 0
                );
                INSERT OR IGNORE INTO agent_state (id, accepting) VALUES (1, 0);
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(model_bindings)").fetchall()
            }
            if "verified_digest" not in columns:
                connection.execute("ALTER TABLE model_bindings ADD COLUMN verified_digest TEXT")
                rows = connection.execute(
                    "SELECT binding_id, binding_json FROM model_bindings WHERE verified = 1"
                ).fetchall()
                for row in rows:
                    binding = ModelBinding.model_validate(json.loads(row["binding_json"]))
                    connection.execute(
                        "UPDATE model_bindings SET verified_digest = ? WHERE binding_id = ?",
                        (_binding_digest(binding), row["binding_id"]),
                    )

    def save_binding(self, binding: ModelBinding) -> None:
        payload = binding.model_dump(mode="json")
        _reject_secrets(payload)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO model_bindings (binding_id, binding_json, verified, verified_digest)
                VALUES (?, ?, 0, NULL)
                ON CONFLICT(binding_id) DO UPDATE SET
                    binding_json = excluded.binding_json,
                    verified = 0,
                    verified_digest = NULL
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

    def mark_verified(
        self,
        binding_id: str,
        verified: bool = True,
        *,
        expected_digest: str | None = None,
    ) -> bool:
        with self.connection() as connection:
            if not verified:
                result = connection.execute(
                    "UPDATE model_bindings SET verified = 0, verified_digest = NULL "
                    "WHERE binding_id = ?",
                    (binding_id,),
                )
                return result.rowcount > 0
            row = connection.execute(
                "SELECT binding_json FROM model_bindings WHERE binding_id = ?", (binding_id,)
            ).fetchone()
            if row is None:
                return False
            binding = ModelBinding.model_validate(json.loads(row["binding_json"]))
            current_digest = _binding_digest(binding)
            if expected_digest is not None and expected_digest != current_digest:
                return False
            result = connection.execute(
                "UPDATE model_bindings SET verified = 1, verified_digest = ? "
                "WHERE binding_id = ? AND binding_json = ?",
                (current_digest, binding_id, row["binding_json"]),
            )
            return result.rowcount > 0

    def is_verified(self, binding_id: str) -> bool:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT binding_json, verified, verified_digest FROM model_bindings "
                "WHERE binding_id = ?",
                (binding_id,),
            ).fetchone()
        if row is None or not row["verified"] or not row["verified_digest"]:
            return False
        binding = ModelBinding.model_validate(json.loads(row["binding_json"]))
        return row["verified_digest"] == _binding_digest(binding)

    def has_verified_enabled_binding(self) -> bool:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT binding_json, verified, verified_digest FROM model_bindings "
                "WHERE verified = 1 AND json_extract(binding_json, '$.enabled') = 1"
            ).fetchall()
        for row in rows:
            if not row["verified_digest"]:
                continue
            binding = ModelBinding.model_validate(json.loads(row["binding_json"]))
            if row["verified_digest"] == _binding_digest(binding):
                return True
        return False

    def binding_digest(self, binding_id: str) -> str:
        binding = self.get_binding(binding_id)
        if binding is None:
            raise KeyError(binding_id)
        return _binding_digest(binding)

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


def _binding_digest(binding: ModelBinding) -> str:
    return canonical_json_digest(binding)
