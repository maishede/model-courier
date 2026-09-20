from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from model_courier.agent.api import create_agent_app
from model_courier.agent.models import ExecutionProfile, ModelBinding
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
            config={"base_url": "http://127.0.0.1:59999", "path": "/transcribe"},
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
