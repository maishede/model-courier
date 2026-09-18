from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from model_courier.contracts import CapabilityManifest
from model_courier.server.app import create_app
from model_courier.server.config import ServerConfig


def build_app(tmp_path: Path):
    app = create_app(ServerConfig.from_root(tmp_path))
    state = app.state.model_courier
    device_token = state.tokens.issue_device("owner-1", "device-1", now=1000)
    worker_token = state.tokens.issue_worker(
        "owner-1",
        "worker-1",
        [
            CapabilityManifest(
                capability_id="worker-1:mock-image",
                task_type="vision.detect.v1",
                provider="mock",
                models=["mock-1"],
                formats=["image/jpeg"],
            )
        ],
        now=1000,
    )
    return app, device_token, worker_token


@pytest.mark.asyncio
async def test_device_to_worker_result_flow(tmp_path: Path) -> None:
    app, device_token, worker_token = build_app(tmp_path)
    transport = httpx.ASGITransport(app=app)
    device_headers = {"Authorization": f"Bearer {device_token}"}
    worker_headers = {"Authorization": f"Bearer {worker_token}"}
    envelope = {
        "task_type": "vision.detect.v1",
        "input_artifacts": [
            {"name": "frame.jpg", "mime": "image/jpeg", "size_bytes": 3}
        ],
        "options": {"confidence": 0.5},
        "idempotency_key": "device-1:frame-1",
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post("/v1/tasks", json=envelope, headers=device_headers)
        assert created.status_code == 201
        task_id = created.json()["task_id"]

        uploaded = await client.put(
            f"/v1/tasks/{task_id}/input",
            params={"name": "frame.jpg", "mime": "image/jpeg", "size_bytes": 3},
            content=b"jpg",
            headers=device_headers,
        )
        assert uploaded.status_code == 200
        assert uploaded.json()["status"] == "queued"

        registration = await client.post(
            "/v1/workers/register",
            json={
                "capabilities": [
                    {
                        "capability_id": "worker-1:mock-image",
                        "task_type": "vision.detect.v1",
                        "provider": "mock",
                        "models": ["mock-1"],
                        "formats": ["image/jpeg"],
                    }
                ]
            },
            headers=worker_headers,
        )
        assert registration.status_code == 200

        polled = await client.post(
            "/v1/workers/poll", json={"wait_seconds": 0}, headers=worker_headers
        )
        assert polled.status_code == 200
        lease = polled.json()
        assert lease["task_id"] == task_id

        started = await client.post(
            f"/v1/workers/tasks/{task_id}/start",
            json={
                "lease_generation": lease["lease_generation"],
                "lease_token": lease["lease_token"],
            },
            headers=worker_headers,
        )
        assert started.status_code == 200

        result = await client.put(
            f"/v1/workers/tasks/{task_id}/result",
            params={
                "lease_generation": lease["lease_generation"],
                "lease_token": lease["lease_token"],
            },
            json={
                "schema_version": "vision.detect.v1",
                "json": {"width": 1, "height": 1, "detections": []},
                "result_digest": "0" * 64,
            },
            headers=worker_headers,
        )
        assert result.status_code == 200

        fetched = await client.get(f"/v1/tasks/{task_id}/result", headers=device_headers)
        assert fetched.status_code == 200
        assert fetched.json()["schema_version"] == "vision.detect.v1"


@pytest.mark.asyncio
async def test_worker_offline_keeps_task_queued_and_principal_isolation(tmp_path: Path) -> None:
    app, device_token, worker_token = build_app(tmp_path)
    other_device = app.state.model_courier.tokens.issue_device("owner-2", "device-2", now=1000)
    transport = httpx.ASGITransport(app=app)
    envelope = {
        "task_type": "vision.detect.v1",
        "input_artifacts": [{"name": "frame.jpg", "mime": "image/jpeg", "size_bytes": 3}],
        "idempotency_key": "frame-offline",
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/v1/tasks", json=envelope, headers={"Authorization": f"Bearer {device_token}"}
        )
        task_id = created.json()["task_id"]
        await client.put(
            f"/v1/tasks/{task_id}/input",
            params={"name": "frame.jpg", "mime": "image/jpeg", "size_bytes": 3},
            content=b"jpg",
            headers={"Authorization": f"Bearer {device_token}"},
        )
        poll = await client.post(
            "/v1/workers/poll",
            json={"wait_seconds": 0},
            headers={"Authorization": f"Bearer {worker_token}"},
        )
        assert poll.status_code == 200
        hidden = await client.get(
            f"/v1/tasks/{task_id}",
            headers={"Authorization": f"Bearer {other_device}"},
        )
        assert hidden.status_code == 404
