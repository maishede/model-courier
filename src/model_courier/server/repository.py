"""Transactional repository for tasks, leases, identities, and artifacts."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from model_courier.contracts import ArtifactRef, CapabilityManifest, ProviderResult, TaskEnvelope

from .db import Database

LEASE_SECONDS = 600
HEARTBEAT_SECONDS = 30
MAX_ATTEMPTS = 3
RESULT_RETENTION_SECONDS = 7 * 24 * 60 * 60


class RepositoryError(RuntimeError):
    """Base class for expected repository failures."""


class NotFoundError(RepositoryError):
    pass


class ConflictError(RepositoryError):
    pass


class LeaseConflictError(ConflictError):
    pass


@dataclass(frozen=True)
class TaskRecord:
    id: str
    owner_id: str
    device_id: str
    task_type: str
    status: str
    request_json: str
    expires_at: float
    execution_deadline: float
    attempt: int
    next_attempt_at: float
    result_available: bool
    result_expires_at: float | None
    error_json: str | None
    result_json: str | None


@dataclass(frozen=True)
class Lease:
    task_id: str
    owner_id: str
    worker_id: str
    lease_generation: int
    lease_token: str
    lease_until: float
    capability_id: str
    request_json: str


def _hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now() -> float:
    return time.time()


def _backoff(attempt: int) -> float:
    return (5, 30, 120)[min(max(attempt - 1, 0), 2)]


def _row_to_task(row: Any) -> TaskRecord:
    return TaskRecord(
        id=row["id"],
        owner_id=row["owner_id"],
        device_id=row["device_id"],
        task_type=row["task_type"],
        status=row["status"],
        request_json=row["request_json"],
        expires_at=row["expires_at"],
        execution_deadline=row["execution_deadline"],
        attempt=row["attempt"],
        next_attempt_at=row["next_attempt_at"],
        result_available=bool(row["result_available"]),
        result_expires_at=row["result_expires_at"],
        error_json=row["error_json"],
        result_json=row["result_json"],
    )


class TaskRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def ensure_owner(self, owner_id: str, now: float | None = None) -> None:
        now = _now() if now is None else now
        with self.database.connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO owners (id, created_at) VALUES (?, ?)", (owner_id, now)
            )

    def ensure_device(
        self, owner_id: str, device_id: str, token_hash: str, now: float | None = None
    ) -> None:
        now = _now() if now is None else now
        with self.database.connection() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO devices "
                "(id, owner_id, token_hash, created_at) VALUES (?, ?, ?, ?)",
                (device_id, owner_id, token_hash, now),
            )

    def ensure_worker(
        self,
        owner_id: str,
        worker_id: str,
        token_hash: str,
        capabilities: Iterable[CapabilityManifest],
        now: float | None = None,
    ) -> None:
        now = _now() if now is None else now
        capabilities_json = json.dumps(
            [capability.model_dump(mode="json") for capability in capabilities],
            sort_keys=True,
        )
        with self.database.connection() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO workers
                    (id, owner_id, token_hash, capabilities_json, last_seen_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (worker_id, owner_id, token_hash, capabilities_json, now, now),
            )

    def update_worker_capabilities(
        self,
        worker_id: str,
        capabilities: Iterable[CapabilityManifest],
        now: float | None = None,
    ) -> None:
        now = _now() if now is None else now
        capabilities_json = json.dumps(
            [capability.model_dump(mode="json") for capability in capabilities],
            sort_keys=True,
        )
        with self.database.connection() as connection:
            updated = connection.execute(
                """
                UPDATE workers
                SET capabilities_json = ?, last_seen_at = ?
                WHERE id = ? AND revoked_at IS NULL
                """,
                (capabilities_json, now, worker_id),
            )
            if updated.rowcount != 1:
                raise NotFoundError("worker not found")

    def worker_capabilities(self, worker_id: str) -> list[CapabilityManifest]:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT capabilities_json FROM workers WHERE id = ? AND revoked_at IS NULL",
                (worker_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError("worker not found")
        return [
            CapabilityManifest.model_validate(item)
            for item in json.loads(row["capabilities_json"])
        ]

    def get_task(self, owner_id: str, task_id: str) -> TaskRecord:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE id = ? AND owner_id = ?", (task_id, owner_id)
            ).fetchone()
        if row is None:
            raise NotFoundError("task not found")
        return _row_to_task(row)

    def input_artifact_path(self, owner_id: str, task_id: str) -> str:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT artifacts.path FROM artifacts
                JOIN tasks ON tasks.id = artifacts.task_id
                WHERE tasks.id = ? AND tasks.owner_id = ? AND artifacts.kind = 'input'
                ORDER BY artifacts.created_at ASC LIMIT 1
                """,
                (task_id, owner_id),
            ).fetchone()
        if row is None:
            raise NotFoundError("input artifact not found")
        return row["path"]

    def create_uploading(
        self,
        owner_id: str,
        device_id: str,
        envelope: TaskEnvelope,
        request_digest: str,
        now: float | None = None,
    ) -> TaskRecord:
        now = _now() if now is None else now
        task_id = uuid.uuid4().hex
        expires_at = now + envelope.ttl_seconds
        request_json = envelope.model_dump_json()
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM tasks
                WHERE owner_id = ? AND device_id = ? AND idempotency_key = ?
                """,
                (owner_id, device_id, envelope.idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["request_digest"] != request_digest:
                    connection.rollback()
                    raise ConflictError("idempotency key was used with a different request")
                connection.commit()
                return _row_to_task(existing)

            connection.execute(
                """
                INSERT INTO tasks (
                    id, owner_id, device_id, task_type, protocol_version, request_json,
                    request_digest, idempotency_key, requested_provider, requested_model,
                    status, expires_at, execution_deadline, next_attempt_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'uploading', ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    owner_id,
                    device_id,
                    envelope.task_type,
                    envelope.protocol_version,
                    request_json,
                    request_digest,
                    envelope.idempotency_key,
                    envelope.requires.provider,
                    envelope.requires.model,
                    expires_at,
                    expires_at,
                    now,
                    now,
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO task_events "
                "(task_id, event_type, details_json, created_at) VALUES (?, ?, ?, ?)",
                (task_id, "created", "{}", now),
            )
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            connection.commit()
        assert row is not None
        return _row_to_task(row)

    def finalize_and_enqueue(
        self,
        task_id: str,
        input_artifact: ArtifactRef,
        request_digest: str,
        artifact_path: str | None = None,
        now: float | None = None,
    ) -> TaskRecord:
        now = _now() if now is None else now
        artifact_id = input_artifact.artifact_id or uuid.uuid4().hex
        if input_artifact.size_bytes is None or input_artifact.sha256 is None:
            raise ConflictError("input artifact must include size_bytes and sha256")
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                connection.rollback()
                raise NotFoundError("task not found")
            if row["request_digest"] != request_digest:
                connection.rollback()
                raise ConflictError("request digest does not match task")
            if row["status"] != "uploading":
                connection.commit()
                return _row_to_task(row)
            if row["expires_at"] <= now:
                connection.execute(
                    "UPDATE tasks SET status = 'expired', updated_at = ? WHERE id = ?",
                    (now, task_id),
                )
                connection.commit()
                raise ConflictError("task has expired")
            connection.execute(
                """
                INSERT INTO artifacts (
                    id, task_id, kind, name, mime, size_bytes, sha256, path,
                    published, expires_at, created_at
                )
                VALUES (?, ?, 'input', ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    artifact_id,
                    task_id,
                    input_artifact.name,
                    input_artifact.mime,
                    input_artifact.size_bytes,
                    input_artifact.sha256,
                    artifact_path or input_artifact.artifact_id or artifact_id,
                    row["expires_at"],
                    now,
                ),
            )
            connection.execute(
                "UPDATE tasks SET status = 'queued', updated_at = ? "
                "WHERE id = ? AND status = 'uploading'",
                (now, task_id),
            )
            connection.execute(
                "INSERT INTO task_events "
                "(task_id, event_type, details_json, created_at) VALUES (?, ?, ?, ?)",
                (task_id, "queued", "{}", now),
            )
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            connection.commit()
        assert row is not None
        return _row_to_task(row)

    @staticmethod
    def _matches(row: Any, capabilities: Iterable[CapabilityManifest]) -> CapabilityManifest | None:
        request = json.loads(row["request_json"])
        requires = request.get("requires") or {}
        requested_provider = requires.get("provider")
        requested_model = requires.get("model")
        requested_formats = set(requires.get("formats") or [])
        input_formats = {item.get("mime") for item in request.get("input_artifacts", [])}
        for capability in capabilities:
            if capability.task_type != row["task_type"]:
                continue
            if requested_provider and capability.provider != requested_provider:
                continue
            if requested_model and requested_model not in capability.models:
                continue
            if requested_formats and not requested_formats.issubset(set(capability.formats)):
                continue
            if (
                input_formats
                and capability.formats
                and not input_formats.issubset(set(capability.formats))
            ):
                continue
            return capability
        return None

    def claim_next(
        self,
        worker_id: str,
        capabilities: Iterable[CapabilityManifest],
        now: float | None = None,
    ) -> Lease | None:
        now = _now() if now is None else now
        capabilities = list(capabilities)
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT * FROM tasks
                WHERE status = 'queued' AND next_attempt_at <= ? AND expires_at > ?
                ORDER BY created_at ASC
                LIMIT 32
                """,
                (now, now),
            ).fetchall()
            for row in rows:
                capability = self._matches(row, capabilities)
                if capability is None:
                    continue
                generation = row["lease_generation"] + 1
                token = secrets.token_urlsafe(32)
                lease_until = min(now + LEASE_SECONDS, row["execution_deadline"])
                updated = connection.execute(
                    """
                    UPDATE tasks
                    SET status = 'leased', attempt = attempt + 1, lease_owner = ?,
                        lease_generation = ?, lease_token_hash = ?, lease_until = ?,
                        bound_capability_id = ?, updated_at = ?
                    WHERE id = ? AND status = 'queued' AND next_attempt_at <= ?
                    """,
                    (
                        worker_id,
                        generation,
                        _hash_token(token),
                        lease_until,
                        capability.capability_id,
                        now,
                        row["id"],
                        now,
                    ),
                )
                if updated.rowcount != 1:
                    continue
                connection.execute(
                    "INSERT INTO task_events "
                    "(task_id, event_type, details_json, created_at) VALUES (?, ?, ?, ?)",
                    (row["id"], "leased", json.dumps({"worker_id": worker_id}), now),
                )
                connection.execute(
                    "UPDATE workers SET last_seen_at = ? WHERE id = ?",
                    (now, worker_id),
                )
                connection.commit()
                return Lease(
                    task_id=row["id"],
                    owner_id=row["owner_id"],
                    worker_id=worker_id,
                    lease_generation=generation,
                    lease_token=token,
                    lease_until=lease_until,
                    capability_id=capability.capability_id,
                    request_json=row["request_json"],
                )
            connection.commit()
        return None

    def _check_lease(self, connection: Any, lease: Lease, now: float) -> Any:
        row = connection.execute("SELECT * FROM tasks WHERE id = ?", (lease.task_id,)).fetchone()
        if row is None:
            raise NotFoundError("task not found")
        if (
            row["owner_id"] != lease.owner_id
            or row["lease_owner"] != lease.worker_id
            or row["lease_generation"] != lease.lease_generation
            or row["lease_token_hash"] != _hash_token(lease.lease_token)
            or row["status"] not in ("leased", "running")
            or row["lease_until"] is None
            or row["lease_until"] <= now
        ):
            raise LeaseConflictError("lease is no longer current")
        if row["execution_deadline"] <= now:
            raise LeaseConflictError("task execution deadline has passed")
        return row

    def start(self, lease: Lease, now: float | None = None) -> TaskRecord:
        now = _now() if now is None else now
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._check_lease(connection, lease, now)
            connection.execute(
                "UPDATE tasks SET status = 'running', lease_until = ?, updated_at = ? WHERE id = ?",
                (min(now + LEASE_SECONDS, row["execution_deadline"]), now, lease.task_id),
            )
            row = connection.execute(
                "SELECT * FROM tasks WHERE id = ?", (lease.task_id,)
            ).fetchone()
            connection.commit()
        assert row is not None
        return _row_to_task(row)

    def heartbeat(self, lease: Lease, now: float | None = None) -> TaskRecord:
        now = _now() if now is None else now
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._check_lease(connection, lease, now)
            lease_until = min(now + LEASE_SECONDS, row["execution_deadline"])
            connection.execute(
                "UPDATE tasks SET lease_until = ?, updated_at = ? WHERE id = ?",
                (lease_until, now, lease.task_id),
            )
            row = connection.execute(
                "SELECT * FROM tasks WHERE id = ?", (lease.task_id,)
            ).fetchone()
            connection.commit()
        assert row is not None
        return _row_to_task(row)

    def fail(
        self,
        lease: Lease,
        error: dict[str, Any],
        retryable: bool,
        now: float | None = None,
    ) -> TaskRecord:
        now = _now() if now is None else now
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._check_lease(connection, lease, now)
            should_retry = retryable and row["attempt"] < MAX_ATTEMPTS and row["expires_at"] > now
            next_status = "queued" if should_retry else "failed"
            next_attempt_at = now + _backoff(row["attempt"]) if should_retry else now
            connection.execute(
                """
                UPDATE tasks
                SET status = ?, next_attempt_at = ?, lease_owner = NULL, lease_token_hash = NULL,
                    lease_until = NULL, error_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    next_status,
                    next_attempt_at,
                    json.dumps(error, sort_keys=True),
                    now,
                    lease.task_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM tasks WHERE id = ?", (lease.task_id,)
            ).fetchone()
            connection.commit()
        assert row is not None
        return _row_to_task(row)

    def publish_result(
        self,
        lease: Lease,
        result: ProviderResult,
        now: float | None = None,
    ) -> TaskRecord:
        now = _now() if now is None else now
        result_json = result.model_dump_json(by_alias=True)
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._check_lease(connection, lease, now)
            if row["status"] not in ("leased", "running"):
                raise LeaseConflictError("task is not publishable")
            connection.execute(
                """
                UPDATE tasks
                SET status = 'succeeded', result_json = ?, result_available = 1,
                    result_expires_at = ?, lease_owner = NULL, lease_token_hash = NULL,
                    lease_until = NULL, updated_at = ?
                WHERE id = ?
                """,
                (result_json, now + RESULT_RETENTION_SECONDS, now, lease.task_id),
            )
            row = connection.execute(
                "SELECT * FROM tasks WHERE id = ?", (lease.task_id,)
            ).fetchone()
            connection.commit()
        assert row is not None
        return _row_to_task(row)

    def remove_input_artifacts(self, owner_id: str, task_id: str) -> list[str]:
        """Remove successful-task input metadata and return stored relative paths."""

        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = connection.execute(
                "SELECT status FROM tasks WHERE id = ? AND owner_id = ?",
                (task_id, owner_id),
            ).fetchone()
            if task is None:
                connection.rollback()
                raise NotFoundError("task not found")
            if task["status"] != "succeeded":
                connection.rollback()
                raise ConflictError("only successful tasks can release input artifacts")
            rows = connection.execute(
                "SELECT path FROM artifacts WHERE task_id = ? AND kind = 'input'",
                (task_id,),
            ).fetchall()
            connection.execute(
                "DELETE FROM artifacts WHERE task_id = ? AND kind = 'input'",
                (task_id,),
            )
            connection.commit()
        return [row["path"] for row in rows]

    def requeue_expired(self, now: float | None = None) -> int:
        now = _now() if now is None else now
        changed = 0
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE tasks SET status = 'expired', updated_at = ? "
                "WHERE status IN ('uploading', 'queued') AND expires_at <= ?",
                (now, now),
            )
            changed += connection.total_changes
            connection.execute(
                """
                UPDATE tasks
                SET status = 'queued', next_attempt_at = ?, lease_owner = NULL,
                    lease_token_hash = NULL, lease_until = NULL, updated_at = ?
                WHERE status IN ('leased', 'running') AND lease_until <= ?
                  AND expires_at > ? AND attempt < ?
                """,
                (now, now, now, now, MAX_ATTEMPTS),
            )
            changed += connection.total_changes
            connection.execute(
                """
                UPDATE tasks
                SET status = 'failed', error_json = ?, lease_owner = NULL,
                    lease_token_hash = NULL, lease_until = NULL, updated_at = ?
                WHERE status IN ('leased', 'running') AND lease_until <= ?
                  AND (expires_at <= ? OR attempt >= ?)
                """,
                (json.dumps({"code": "attempts_exhausted"}), now, now, now, MAX_ATTEMPTS),
            )
            changed += connection.total_changes
            connection.commit()
        return changed

    def cancel(self, owner_id: str, task_id: str, now: float | None = None) -> TaskRecord:
        now = _now() if now is None else now
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM tasks WHERE id = ? AND owner_id = ?", (task_id, owner_id)
            ).fetchone()
            if row is None:
                connection.rollback()
                raise NotFoundError("task not found")
            if row["status"] in ("succeeded", "failed", "expired", "canceled"):
                connection.commit()
                return _row_to_task(row)
            connection.execute(
                """
                UPDATE tasks
                SET status = 'canceled', lease_owner = NULL, lease_token_hash = NULL,
                    lease_until = NULL, updated_at = ?
                WHERE id = ? AND owner_id = ?
                """,
                (now, task_id, owner_id),
            )
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            connection.commit()
        assert row is not None
        return _row_to_task(row)
