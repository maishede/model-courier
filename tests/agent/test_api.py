from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from model_courier.agent.api import create_agent_app
from model_courier.agent.models import ExecutionProfile, ModelBinding
from model_courier.agent.runtime import AgentRuntimeController
from model_courier.agent.store import AgentStore


def binding(enabled: bool = True) -> ModelBinding:
    return ModelBinding(
        binding_id="local-funasr",
        display_name="中文语音识别",
        service_id="desktop-gpu-1",
        execution=ExecutionProfile(
            profile_id="funasr-http",
            kind="http",
            adapter_id="http.transcribe.v1",
            config={
                "base_url": "http://127.0.0.1:59999",
                "path": "/transcribe",
                "response_path": "result.text",
            },
        ),
        model_name="paraformer",
        enabled=enabled,
    )


@pytest.mark.asyncio
async def test_agent_api_requires_session_token_and_persists_public_profile(tmp_path: Path) -> None:
    token = "session-token-1234567890"
    app = create_agent_app(tmp_path / "agent.sqlite3", session_token=token)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://agent") as client:
        unauthorized = await client.get("/v1/models")
        assert unauthorized.status_code == 401

        saved = await client.post(
            "/v1/models",
            json=binding().model_dump(mode="json"),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert saved.status_code == 201
        listed = await client.get(
            "/v1/models", headers={"Authorization": f"Bearer {token}"}
        )

    assert listed.json()[0]["display_name"] == "中文语音识别"
    assert listed.json()[0]["verified"] is False
    raw = AgentStore(tmp_path / "agent.sqlite3").raw_binding_json("local-funasr")
    assert "token" not in raw.lower()


@pytest.mark.asyncio
async def test_accepting_requires_a_verified_enabled_model(tmp_path: Path) -> None:
    token = "session-token-1234567890"
    app = create_agent_app(tmp_path / "agent.sqlite3", session_token=token)
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(transport=transport, base_url="http://agent") as client:
        await client.post("/v1/models", json=binding().model_dump(mode="json"), headers=headers)
        refused = await client.post("/v1/accepting", json={"enabled": True}, headers=headers)
        assert refused.status_code == 409
        test_result = await client.post("/v1/models/local-funasr/test", headers=headers)

    assert test_result.json()["stage"] == "connection"
    assert test_result.json()["error"]["code"] in {"connection_error", "http_error"}


@pytest.mark.asyncio
async def test_agent_rejects_plaintext_secret_and_accepting_start_is_idempotent(
    tmp_path: Path,
) -> None:
    token = "session-token-1234567890"
    app = create_agent_app(
        tmp_path / "agent.sqlite3",
        session_token=token,
        runtime=AgentRuntimeController(_IdleWorkerRuntime()),
    )
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {token}"}
    payload = binding().model_dump(mode="json")
    payload["execution"]["config"]["authorization"] = "secret-value"

    async with httpx.AsyncClient(transport=transport, base_url="http://agent") as client:
        rejected = await client.post("/v1/models", json=payload, headers=headers)
        assert rejected.status_code == 422
        app.state.model_courier_agent.store.save_binding(binding())
        app.state.model_courier_agent.store.mark_verified("local-funasr")
        first = await client.post("/v1/accepting", json={"enabled": True}, headers=headers)
        second = await client.post("/v1/accepting", json={"enabled": True}, headers=headers)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() == {"accepting": True}


@pytest.mark.asyncio
async def test_agent_rejects_accepting_without_configured_runtime(tmp_path: Path) -> None:
    token = "session-token-1234567890"
    app = create_agent_app(tmp_path / "agent.sqlite3", session_token=token)
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(transport=transport, base_url="http://agent") as client:
        await client.post("/v1/models", json=binding().model_dump(mode="json"), headers=headers)
        app.state.model_courier_agent.store.mark_verified("local-funasr")
        response = await client.post("/v1/accepting", json={"enabled": True}, headers=headers)
        status = await client.get("/v1/status", headers=headers)

    assert response.status_code == 503
    assert status.json() == {"accepting": False, "runtime": "unconfigured"}


def test_agent_platform_configuration_creates_runtime_controller(tmp_path: Path) -> None:
    app = create_agent_app(
        tmp_path / "agent.sqlite3",
        platform_url="https://relay.example.com",
        worker_token="worker-token",
    )

    assert app.state.model_courier_agent.runtime.status() == "stopped"


@pytest.mark.asyncio
async def test_preflight_does_not_mark_python_model_verified(tmp_path: Path) -> None:
    token = "session-token-1234567890"
    app = create_agent_app(tmp_path / "agent.sqlite3", session_token=token)
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {token}"}
    python_binding = ModelBinding(
        binding_id="local-funasr",
        display_name="中文语音识别",
        service_id="desktop-gpu-1",
        execution=ExecutionProfile(
            profile_id="system-python",
            kind="python",
            adapter_id="python.bridge.v1",
            config={"executable": sys.executable},
        ),
        model_name="paraformer",
        enabled=True,
    )

    async with httpx.AsyncClient(transport=transport, base_url="http://agent") as client:
        await client.post(
            "/v1/models", json=python_binding.model_dump(mode="json"), headers=headers
        )
        preflight = await client.post("/v1/models/local-funasr/test", headers=headers)
        refused = await client.post("/v1/accepting", json={"enabled": True}, headers=headers)
        verification = await client.post(
            "/v1/models/local-funasr/verify",
            content=b"sample",
            headers={**headers, "Content-Type": "audio/wav"},
        )

    assert preflight.json()["status"] == "succeeded"
    assert refused.status_code == 409
    assert verification.json()["error"]["code"] == "bridge_no_response"


@pytest.mark.asyncio
async def test_http_verification_runs_inference_before_marking_enabled(
    tmp_path: Path,
) -> None:
    token = "session-token-1234567890"
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"result": {"text": "verified"}})
        )
    )
    app = create_agent_app(
        tmp_path / "agent.sqlite3", session_token=token, http_client=client
    )
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(transport=transport, base_url="http://agent") as api_client:
        await api_client.post("/v1/models", json=binding().model_dump(mode="json"), headers=headers)
        verification = await api_client.post(
            "/v1/models/local-funasr/verify",
            content=b"sample",
            headers={**headers, "Content-Type": "audio/wav"},
        )

    assert verification.status_code == 200
    assert verification.json()["stage"] == "inference"
    assert verification.json()["result"]["json"] == {"text": "verified"}
    assert app.state.model_courier_agent.store.has_verified_enabled_binding() is True


@pytest.mark.asyncio
async def test_python_verification_runs_configured_bridge(tmp_path: Path) -> None:
    bridge = tmp_path / "verify_bridge.py"
    bridge.write_text(
        "import json, sys\n"
        "request = json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'request_id': request['request_id'], "
        "'json': {'text': 'python-verified'}}), flush=True)\n",
        encoding="utf-8",
    )
    token = "session-token-1234567890"
    app = create_agent_app(tmp_path / "agent.sqlite3", session_token=token)
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {token}"}
    python_binding = ModelBinding(
        binding_id="local-python",
        display_name="Python Model",
        service_id="desktop-gpu-1",
        execution=ExecutionProfile(
            profile_id="system-python",
            kind="python",
            adapter_id="python.bridge.v1",
            config={"executable": sys.executable, "arguments": [str(bridge)]},
        ),
        model_name="demo",
        enabled=True,
    )

    async with httpx.AsyncClient(transport=transport, base_url="http://agent") as client:
        await client.post(
            "/v1/models", json=python_binding.model_dump(mode="json"), headers=headers
        )
        verification = await client.post(
            "/v1/models/local-python/verify",
            content=b"sample",
            headers={**headers, "Content-Type": "audio/wav"},
        )

    assert verification.json()["status"] == "succeeded"
    assert verification.json()["result"]["json"] == {"text": "python-verified"}


def test_saving_binding_while_accepting_pauses_runtime(tmp_path: Path) -> None:
    token = "session-token-1234567890"
    app = create_agent_app(
        tmp_path / "agent.sqlite3",
        session_token=token,
        runtime=AgentRuntimeController(_IdleWorkerRuntime()),
    )
    store = app.state.model_courier_agent.store
    store.save_binding(binding())
    store.mark_verified("local-funasr")
    store.set_accepting(True)

    with TestClient(app) as client:
        response = client.post(
            "/v1/models",
            json=binding().model_dump(mode="json"),
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 201
    assert store.accepting() is False


class _IdleWorkerRuntime:
    def run_forever(self, stop_event: object) -> None:
        return None
