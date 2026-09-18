"""Deterministic Provider used for protocol and integration tests."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from model_courier.contracts import CapabilityManifest, ProviderResult, TaskEnvelope
from model_courier.worker.factory import ProviderRegistration
from model_courier.worker.provider import ExecutionContext, ProviderError


def _digest(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class MockProvider:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def describe(self) -> CapabilityManifest:
        return CapabilityManifest(
            capability_id="mock:generic",
            task_type="vision.detect.v1",
            provider="mock",
            models=["mock-1"],
            formats=["image/jpeg", "image/png"],
        )

    def validate(self, task: TaskEnvelope) -> None:
        if task.task_type not in {"vision.detect.v1", "audio.transcribe.v1"}:
            raise ProviderError("unsupported_task", "mock provider cannot handle this task")

    def execute(self, task: TaskEnvelope, context: ExecutionContext) -> ProviderResult:
        context.report_progress(0.5, "mock provider executed")
        if task.task_type == "audio.transcribe.v1":
            payload = {
                "text": self.config.get("text", "mock transcription"),
                "language": "und",
                "segments": [],
            }
            schema = "audio.transcribe.v1"
        else:
            payload = {"width": 1, "height": 1, "detections": []}
            schema = "vision.detect.v1"
        return ProviderResult(schema_version=schema, json=payload, result_digest=_digest(payload))

    def close(self) -> None:
        return None


def mock_registration() -> ProviderRegistration:
    capability = CapabilityManifest(
        capability_id="mock:generic",
        task_type="vision.detect.v1",
        provider="mock",
        models=["mock-1"],
        formats=["image/jpeg", "image/png"],
    )
    return ProviderRegistration(capability, MockProvider)


def mock_audio_registration() -> ProviderRegistration:
    capability = CapabilityManifest(
        capability_id="mock:audio",
        task_type="audio.transcribe.v1",
        provider="mock",
        models=["mock-1"],
        formats=["audio/wav", "audio/mpeg"],
    )
    return ProviderRegistration(capability, MockProvider)
