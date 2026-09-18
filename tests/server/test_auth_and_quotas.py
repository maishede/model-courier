from __future__ import annotations

from pathlib import Path

import pytest

from model_courier.contracts import CapabilityManifest
from model_courier.server.auth import AuthenticationError, TokenStore
from model_courier.server.db import Database
from model_courier.server.quotas import QuotaExceededError, QuotaManager


def setup_db(tmp_path: Path) -> Database:
    database = Database.open(tmp_path / "control.sqlite3")
    database.initialize()
    return database


def test_device_and_worker_tokens_are_scoped_and_revocable(tmp_path: Path) -> None:
    database = setup_db(tmp_path)
    tokens = TokenStore(database)
    device_token = tokens.issue_device("owner-1", "device-1", now=1000)
    worker_token = tokens.issue_worker(
        "owner-1",
        "worker-1",
        [
            CapabilityManifest(
                capability_id="worker-1:mock",
                task_type="audio.transcribe.v1",
                provider="mock",
            )
        ],
        now=1000,
    )

    assert tokens.authenticate("device", device_token).subject_id == "device-1"
    assert tokens.authenticate("worker", worker_token).subject_id == "worker-1"
    tokens.revoke("device", "device-1", now=1001)
    with pytest.raises(AuthenticationError):
        tokens.authenticate("device", device_token)


def test_quota_rejects_too_many_tasks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = setup_db(tmp_path)
    root = tmp_path / "artifacts"
    root.mkdir()
    manager = QuotaManager(database, root)
    monkeypatch.setattr("model_courier.server.quotas.MIN_FREE_BYTES", 0)

    with database.connection() as connection:
        connection.execute(
            "INSERT INTO owners (id, created_at) VALUES (?, ?)", ("owner-1", 1000)
        )
        connection.execute(
            "INSERT INTO devices (id, owner_id, token_hash, created_at) VALUES (?, ?, ?, ?)",
            ("device-1", "owner-1", "hash", 1000),
        )
        for index in range(10):
            connection.execute(
                """
                INSERT INTO tasks (
                    id, owner_id, device_id, task_type, protocol_version, request_json,
                    request_digest, idempotency_key, status, expires_at, execution_deadline,
                    next_attempt_at, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, 'audio.transcribe.v1', '1', '{}', ?, ?, 'queued',
                    2000, 2000, 1000, 1000, 1000
                )
                """,
                (f"task-{index}", "owner-1", "device-1", f"digest-{index}", f"key-{index}"),
            )

    with pytest.raises(QuotaExceededError, match="device"):
        manager.reserve("owner-1", "device-1", 1)
