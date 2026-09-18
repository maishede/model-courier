from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from model_courier.contracts import (
    ArtifactRef,
    CapabilityManifest,
    ProviderResult,
    TaskEnvelope,
    TaskRequirements,
)
from model_courier.server.db import Database
from model_courier.server.repository import (
    ConflictError,
    LeaseConflictError,
    NotFoundError,
    TaskRepository,
)


def make_repo(tmp_path: Path) -> TaskRepository:
    database = Database.open(tmp_path / "model-courier.sqlite3")
    database.initialize()
    repo = TaskRepository(database)
    repo.ensure_owner("owner-1", now=1000)
    repo.ensure_device("owner-1", "device-1", "device-token-hash", now=1000)
    repo.ensure_worker(
        "owner-1",
        "worker-1",
        "worker-token-hash",
        [
            CapabilityManifest(
                capability_id="worker-1:mock-audio",
                task_type="audio.transcribe.v1",
                provider="mock",
                models=["mock-1"],
                formats=["audio/wav"],
            )
        ],
        now=1000,
    )
    return repo


def make_task(repo: TaskRepository, key: str = "sample-1", now: float = 1000):
    envelope = TaskEnvelope(
        task_type="audio.transcribe.v1",
        input_artifacts=[ArtifactRef(name="input.wav", mime="audio/wav", size_bytes=3)],
        requires=TaskRequirements(provider="mock", model="mock-1"),
        idempotency_key=key,
        ttl_seconds=1000,
    )
    digest = "a" * 64
    task = repo.create_uploading("owner-1", "device-1", envelope, digest, now=now)
    return repo.finalize_and_enqueue(
        task.id,
        ArtifactRef(
            artifact_id="input-1",
            name="input.wav",
            mime="audio/wav",
            size_bytes=3,
            sha256="b" * 64,
        ),
        digest,
        now=now,
    )


def test_uploading_task_is_not_claimable_until_finalized(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    envelope = TaskEnvelope(
        task_type="audio.transcribe.v1",
        input_artifacts=[ArtifactRef(name="input.wav", mime="audio/wav")],
        idempotency_key="uploading",
    )
    task = repo.create_uploading("owner-1", "device-1", envelope, "c" * 64, now=1000)

    assert repo.claim_next("worker-1", [], now=1001) is None
    assert task.status == "uploading"


def test_claim_and_start_are_lease_bound(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    task = make_task(repo)
    capabilities = [
        CapabilityManifest(
            capability_id="worker-1:mock-audio",
            task_type="audio.transcribe.v1",
            provider="mock",
            models=["mock-1"],
            formats=["audio/wav"],
        )
    ]

    lease = repo.claim_next("worker-1", capabilities, now=1001)
    assert lease is not None
    assert lease.task_id == task.id
    assert repo.start(lease, now=1002).status == "running"

    with pytest.raises(LeaseConflictError):
        repo.heartbeat(lease, now=2000)


def test_concurrent_claim_has_one_winner(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    make_task(repo, key="race")
    capabilities = [
        CapabilityManifest(
            capability_id="worker-1:mock-audio",
            task_type="audio.transcribe.v1",
            provider="mock",
            models=["mock-1"],
            formats=["audio/wav"],
        )
    ]
    barrier = threading.Barrier(2)

    def claim(worker_id: str):
        barrier.wait()
        return repo.claim_next(worker_id, capabilities, now=1001)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ["worker-1", "worker-1"]))

    assert sum(result is not None for result in results) == 1


def test_retry_requeues_and_old_lease_cannot_publish(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    task = make_task(repo, key="retry")
    capability = CapabilityManifest(
        capability_id="worker-1:mock-audio",
        task_type="audio.transcribe.v1",
        provider="mock",
        models=["mock-1"],
        formats=["audio/wav"],
    )
    first = repo.claim_next("worker-1", [capability], now=1001)
    assert first is not None
    repo.start(first, now=1002)
    assert repo.requeue_expired(now=1700) >= 1

    with pytest.raises(LeaseConflictError):
        repo.heartbeat(first, now=1701)

    second = repo.claim_next("worker-1", [capability], now=1701)
    assert second is not None
    assert second.lease_generation > first.lease_generation
    assert second.task_id == task.id


def test_idempotency_conflict_is_rejected(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    first = make_task(repo, key="same")
    envelope = TaskEnvelope(
        task_type="vision.detect.v1",
        input_artifacts=[ArtifactRef(name="input.jpg", mime="image/jpeg")],
        idempotency_key="same",
    )

    with pytest.raises(ConflictError):
        repo.create_uploading("owner-1", "device-1", envelope, "d" * 64, now=1001)

    assert first.status == "queued"


def test_successful_task_releases_input_artifact_metadata(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    task = make_task(repo, key="release-input")
    capability = repo.worker_capabilities("worker-1")[0]
    lease = repo.claim_next("worker-1", [capability], now=1001)
    assert lease is not None
    repo.start(lease, now=1002)
    repo.publish_result(
        lease,
        ProviderResult(
            schema_version="audio.transcribe.v1",
            json={"text": "ok"},
            result_digest="0" * 64,
        ),
        now=1003,
    )

    assert repo.remove_input_artifacts("owner-1", task.id) == ["input-1"]
    with pytest.raises(NotFoundError):
        repo.input_artifact_path("owner-1", task.id)
