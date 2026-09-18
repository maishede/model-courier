from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from model_courier.contracts import CapabilityManifest
from model_courier.server.app import create_app
from model_courier.server.config import ServerConfig


def _build_app(root: Path):
    app = create_app(ServerConfig.from_root(root))
    state = app.state.model_courier
    device_token = state.tokens.issue_device("owner-1", "device-1", now=1000)
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
        now=1000,
    )
    return app, device_token, worker_token


@pytest.mark.asyncio
async def test_mock_pipeline_preserves_device_request_shape(tmp_path: Path) -> None:
    app, device_token, worker_token = _build_app(tmp_path)
    state = app.state.model_courier
    transport = httpx.ASGITransport(app=app)
    device_headers = {"Authorization": f"Bearer {device_token}"}
    worker_headers = {"Authorization": f"Bearer {worker_token}"}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/v1/tasks",
            headers=device_headers,
            json={
                "task_type": "vision.detect.v1",
                "input_artifacts": [
                    {"name": "frame.jpg", "mime": "image/jpeg", "size_bytes": 3}
                ],
                "options": {"confidence": 0.5},
                "idempotency_key": "camera:0001",
            },
        )
        assert created.status_code == 201
        task_id = created.json()["task_id"]
        uploaded = await client.put(
            f"/v1/tasks/{task_id}/input",
            headers=device_headers,
            params={"name": "frame.jpg", "mime": "image/jpeg", "size_bytes": 3},
            content=b"jpg",
        )
        assert uploaded.json()["status"] == "queued"
        input_path = state.storage.root / state.repository.input_artifact_path(
            "owner-1", task_id
        )
        assert input_path.is_file()

        lease_response = await client.post(
            "/v1/workers/poll",
            headers=worker_headers,
            json={"wait_seconds": 0},
        )
        lease = lease_response.json()
        assert lease["task"]["task_type"] == "vision.detect.v1"
        assert lease["task"]["options"] == {"confidence": 0.5}

        await client.post(
            f"/v1/workers/tasks/{task_id}/start",
            headers=worker_headers,
            json={
                "lease_generation": lease["lease_generation"],
                "lease_token": lease["lease_token"],
            },
        )
        published = await client.put(
            f"/v1/workers/tasks/{task_id}/result",
            headers=worker_headers,
            params={
                "lease_generation": lease["lease_generation"],
                "lease_token": lease["lease_token"],
            },
            json={
                "schema_version": "vision.detect.v1",
                "json": {"width": 1, "height": 1, "detections": []},
                "result_digest": "0" * 64,
            },
        )
        assert published.json()["status"] == "succeeded"
        result = await client.get(f"/v1/tasks/{task_id}/result", headers=device_headers)
        assert result.json()["schema_version"] == "vision.detect.v1"
        assert not input_path.exists()
