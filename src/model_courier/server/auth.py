"""Scoped bearer-token management for devices and Workers."""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from typing import Literal

from model_courier.contracts import CapabilityManifest

from .db import Database

PrincipalKind = Literal["device", "worker"]


@dataclass(frozen=True)
class Principal:
    kind: PrincipalKind
    subject_id: str
    owner_id: str


class AuthenticationError(ValueError):
    pass


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class TokenStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def issue_device(self, owner_id: str, device_id: str, now: float) -> str:
        token = secrets.token_urlsafe(32)
        with self.database.connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO owners (id, created_at) VALUES (?, ?)", (owner_id, now)
            )
            connection.execute(
                """
                INSERT INTO devices (id, owner_id, token_hash, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET owner_id = excluded.owner_id,
                    token_hash = excluded.token_hash, revoked_at = NULL
                """,
                (device_id, owner_id, hash_token(token), now),
            )
        return token

    def issue_worker(
        self,
        owner_id: str,
        worker_id: str,
        capabilities: list[CapabilityManifest],
        now: float,
    ) -> str:
        token = secrets.token_urlsafe(32)
        with self.database.connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO owners (id, created_at) VALUES (?, ?)", (owner_id, now)
            )
            connection.execute(
                """
                INSERT INTO workers (
                    id, owner_id, token_hash, capabilities_json, created_at, last_seen_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET owner_id = excluded.owner_id,
                    token_hash = excluded.token_hash,
                    capabilities_json = excluded.capabilities_json,
                    revoked_at = NULL
                """,
                (
                    worker_id,
                    owner_id,
                    hash_token(token),
                    json.dumps([item.model_dump(mode="json") for item in capabilities]),
                    now,
                    now,
                ),
            )
        return token

    def authenticate(self, kind: PrincipalKind, token: str) -> Principal:
        token_hash = hash_token(token)
        table = "devices" if kind == "device" else "workers"
        with self.database.connection() as connection:
            row = connection.execute(
                f"SELECT id, owner_id, revoked_at FROM {table} WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
        if row is None or row["revoked_at"] is not None:
            raise AuthenticationError("invalid or revoked token")
        return Principal(kind, row["id"], row["owner_id"])

    def revoke(self, kind: PrincipalKind, subject_id: str, now: float) -> None:
        table = "devices" if kind == "device" else "workers"
        with self.database.connection() as connection:
            connection.execute(f"UPDATE {table} SET revoked_at = ? WHERE id = ?", (now, subject_id))
