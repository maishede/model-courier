from __future__ import annotations

import pytest
from pydantic import ValidationError

from model_courier.contracts import (
    ArtifactRef,
    CapabilityManifest,
    ErrorEnvelope,
    ProviderResult,
    TaskEnvelope,
    TaskRequirements,
    canonical_json_digest,
)


def artifact(name: str = "input.wav", mime: str = "audio/wav") -> ArtifactRef:
    return ArtifactRef(name=name, mime=mime, size_bytes=12)


def test_audio_task_round_trips_with_requirements() -> None:
    task = TaskEnvelope(
        task_type="audio.transcribe.v1",
        input_artifacts=[artifact()],
        requires=TaskRequirements(
            provider="funasr", model="paraformer", service_id="desktop-gpu-1"
        ),
        options={"language": "zh"},
        idempotency_key="device-1:sample-1",
    )

    assert task.model_dump(mode="json")["task_type"] == "audio.transcribe.v1"
    assert task.requires.provider == "funasr"
    assert task.requires.service_id == "desktop-gpu-1"
    assert len(canonical_json_digest(task)) == 64


def test_vision_task_accepts_image_artifact() -> None:
    task = TaskEnvelope(
        task_type="vision.detect.v1",
        input_artifacts=[artifact("frame.jpg", "image/jpeg")],
        idempotency_key="frame-1",
    )

    assert task.ttl_seconds == 86_400


@pytest.mark.parametrize(
    "overrides",
    [
        {"task_type": "unknown.v1"},
        {"protocol_version": "2"},
        {"idempotency_key": "contains spaces"},
        {"ttl_seconds": 0},
        {"input_artifacts": []},
        {"unexpected": True},
    ],
)
def test_task_rejects_invalid_contract(overrides: dict) -> None:
    payload = {
        "task_type": "audio.transcribe.v1",
        "input_artifacts": [artifact().model_dump()],
        "idempotency_key": "ok",
        **overrides,
    }

    with pytest.raises(ValidationError):
        TaskEnvelope.model_validate(payload)


def test_capability_and_result_contracts_are_strict() -> None:
    capability = CapabilityManifest(
        capability_id="worker-1:funasr",
        task_type="audio.transcribe.v1",
        provider="funasr",
        models=["paraformer"],
        formats=["audio/wav"],
        service_id="desktop-gpu-1",
    )
    result = ProviderResult(
        schema_version="audio.transcribe.v1",
        json={"text": "你好", "language": "zh", "segments": []},
        result_digest="0" * 64,
    )
    error = ErrorEnvelope(
        code="provider_timeout",
        message="timed out",
        retryable=True,
        request_id="req-1",
    )

    assert capability.max_concurrency == 1
    assert capability.service_id == "desktop-gpu-1"
    assert result.json["text"] == "你好"
    assert error.retryable is True
