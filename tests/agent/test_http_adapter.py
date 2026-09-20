from __future__ import annotations

import httpx
import pytest

from model_courier.agent.http_adapter import HttpAdapterConfig, HttpAdapterError, HttpModelAdapter
from model_courier.contracts import ArtifactRef, TaskEnvelope


def task() -> TaskEnvelope:
    return TaskEnvelope(
        task_type="audio.transcribe.v1",
        input_artifacts=[ArtifactRef(name="speech.wav", mime="audio/wav", size_bytes=4)],
        idempotency_key="http-1",
    )


def test_http_adapter_maps_multipart_response_to_provider_result() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["body"] = request.content.decode("utf-8", errors="ignore")
        return httpx.Response(200, json={"result": {"text": "你好"}})

    adapter = HttpModelAdapter(
        HttpAdapterConfig(
            base_url="http://127.0.0.1:9000",
            path="/transcribe",
            response_path="result.text",
            model_field="model",
            model_name="paraformer",
        ),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = adapter.execute(task(), b"wav")

    assert seen["method"] == "POST"
    assert "name=\"speech.wav\"" in seen["body"]
    assert "name=\"model\"" in seen["body"]
    assert result.schema_version == "audio.transcribe.v1"
    assert result.json == {"text": "你好"}
    assert len(result.result_digest) == 64


def test_http_adapter_rejects_missing_response_path() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
    )
    adapter = HttpModelAdapter(
        HttpAdapterConfig(base_url="http://127.0.0.1:9000", path="/transcribe"), client=client
    )

    with pytest.raises(HttpAdapterError, match="response_field_missing"):
        adapter.execute(task(), b"wav")


def test_http_adapter_enforces_response_limit_and_status() -> None:
    oversized = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 33))
    )
    adapter = HttpModelAdapter(
        HttpAdapterConfig(
            base_url="http://127.0.0.1:9000", path="/transcribe", max_response_bytes=32
        ),
        client=oversized,
    )
    with pytest.raises(HttpAdapterError, match="response_too_large"):
        adapter.execute(task(), b"wav")

    failed = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(503, text="busy"))
    )
    adapter = HttpModelAdapter(
        HttpAdapterConfig(base_url="http://127.0.0.1:9000", path="/transcribe"), client=failed
    )
    with pytest.raises(HttpAdapterError, match="http_error"):
        adapter.execute(task(), b"wav")
