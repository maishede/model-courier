from __future__ import annotations

import time
from pathlib import Path

import pytest

from model_courier.contracts import CapabilityManifest, ProviderResult, TaskEnvelope
from model_courier.worker.process_runner import ProcessRunner
from model_courier.worker.provider import ExecutionContext, ProviderError


class SleepProvider:
    def __init__(self, config: dict[str, float]) -> None:
        self.config = config

    def describe(self) -> CapabilityManifest:
        return CapabilityManifest(
            capability_id="test:sleep",
            task_type="vision.detect.v1",
            provider="test",
            models=["sleep"],
            formats=["image/jpeg"],
        )

    def validate(self, task: TaskEnvelope) -> None:
        return None

    def execute(self, task: TaskEnvelope, context: ExecutionContext) -> ProviderResult:
        time.sleep(self.config.get("seconds", 0.0))
        return ProviderResult(
            schema_version="vision.detect.v1",
            json={"width": 1, "height": 1, "detections": []},
            result_digest="0" * 64,
        )

    def close(self) -> None:
        return None


def _task() -> TaskEnvelope:
    return TaskEnvelope(
        task_type="vision.detect.v1",
        input_artifacts=[{"name": "frame.jpg", "mime": "image/jpeg"}],
        idempotency_key="runner-test",
    )


def test_process_runner_returns_result_from_child_process() -> None:
    result = ProcessRunner().execute(SleepProvider({}), _task(), "task-1", [Path("frame.jpg")], 2.0)

    assert result.schema_version == "vision.detect.v1"
    assert result.json["detections"] == []


def test_process_runner_terminates_provider_past_deadline() -> None:
    with pytest.raises(ProviderError, match="execution deadline") as error:
        ProcessRunner().execute(
            SleepProvider({"seconds": 1.0}),
            _task(),
            "task-2",
            [],
            0.05,
        )

    assert error.value.code == "provider_timeout"
    assert error.value.retryable is True
