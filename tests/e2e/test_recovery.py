from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest

from model_courier.contracts import CapabilityManifest
from model_courier.server.app import create_app
from model_courier.server.config import ServerConfig


@pytest.mark.asyncio
async def test_expired_lease_is_reclaimed_and_old_worker_is_rejected(tmp_path: Path) -> None:
    app = create_app(ServerConfig.from_root(tmp_path))
    state = app.state.model_courier
    device_token = state.tokens.issue_device("owner-1", "device-1", now=time.time())
    worker_token = state.tokens.issue_worker(
        "owner-1",
        "worker-1",
        [
            CapabilityManifest(
                capability_id="mock:generic",
                task_type="vision.detect.v1",
                provider="mock",
                models=["mock-1"],
                formats=["image/jpeg"],
            )
        ],
        now=time.time(),
    )
    device_headers = {"Authorization": f"Bearer {device_token}"}
    worker_headers = {"Authorization": f"Bearer {worker_token}"}
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/v1/tasks",
            headers=device_headers,
            json={
                "task_type": "vision.detect.v1",
                "input_artifacts": [{"name": "frame.jpg", "mime": "image/jpeg"}],
                "idempotency_key": "recover:0001",
            },
        )
        task_id = created.json()["task_id"]
        await client.put(
            f"/v1/tasks/{task_id}/input",
            headers=device_headers,
            params={"name": "frame.jpg", "mime": "image/jpeg", "size_bytes": 3},
            content=b"jpg",
        )
        first = (
            await client.post(
                "/v1/workers/poll",
                headers=worker_headers,
                json={"wait_seconds": 0},
            )
        ).json()
        expired_now = first["lease_until"] + 1
        state.repository.requeue_expired(now=expired_now)
        second_lease = state.repository.claim_next(
            "worker-1",
            state.repository.worker_capabilities("worker-1"),
            now=expired_now,
        )
        assert second_lease is not None
        assert second_lease.lease_generation > first["lease_generation"]

        stale = await client.post(
            f"/v1/workers/tasks/{task_id}/start",
            headers=worker_headers,
            json={
                "lease_generation": first["lease_generation"],
                "lease_token": first["lease_token"],
            },
        )
        assert stale.status_code == 409

        current = await client.post(
            f"/v1/workers/tasks/{task_id}/start",
            headers=worker_headers,
            json={
                "lease_generation": second_lease.lease_generation,
                "lease_token": second_lease.lease_token,
            },
        )
        assert current.status_code == 200
