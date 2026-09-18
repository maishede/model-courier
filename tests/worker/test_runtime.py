from __future__ import annotations

import httpx

from model_courier.contracts import ArtifactRef, TaskEnvelope
from model_courier.providers.mock import mock_registration
from model_courier.worker.factory import ProviderFactory
from model_courier.worker.runtime import WorkerConfig, WorkerRuntime


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

        def response(method: str, url: str, **kwargs):
            return httpx.Response(
                kwargs.pop("status_code", 200),
                request=httpx.Request(method, f"http://test{url}"),
                **kwargs,
            )

        self.responses = [
            response(
                "POST",
                "/v1/workers/poll",
                json={
                    "task_id": "task-1",
                    "lease_generation": 1,
                    "lease_token": "t" * 32,
                    "lease_until": 9999,
                    "capability_id": "mock:generic",
                    "task": TaskEnvelope(
                        task_type="vision.detect.v1",
                        input_artifacts=[ArtifactRef(name="frame.jpg", mime="image/jpeg")],
                        idempotency_key="frame-1",
                    ).model_dump(mode="json"),
                },
            ),
            response("POST", "/v1/workers/tasks/task-1/start", json={"status": "running"}),
            response("GET", "/v1/workers/tasks/task-1/input", content=b"image"),
            response("PUT", "/v1/workers/tasks/task-1/result", json={"status": "succeeded"}),
        ]

    def post(self, url: str, **kwargs):
        self.calls.append(("POST", url))
        return self.responses.pop(0)

    def get(self, url: str, **kwargs):
        self.calls.append(("GET", url))
        return self.responses.pop(0)

    def put(self, url: str, **kwargs):
        self.calls.append(("PUT", url))
        return self.responses.pop(0)


def test_runtime_executes_mock_provider_and_publishes_result() -> None:
    factory = ProviderFactory()
    factory.register(mock_registration())
    client = FakeClient()
    runtime = WorkerRuntime(WorkerConfig("http://test", "token"), factory, client=client)

    outcome = runtime.run_once()

    assert outcome.status == "succeeded"
    assert outcome.task_id == "task-1"
    assert [method for method, _ in client.calls] == ["POST", "POST", "GET", "PUT"]
