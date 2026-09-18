from __future__ import annotations

import httpx
import pytest
from model_courier_sdk import ModelCourierClient, TaskFailedError

from model_courier.contracts import ArtifactRef, TaskEnvelope


def envelope() -> TaskEnvelope:
    return TaskEnvelope(
        task_type="vision.detect.v1",
        input_artifacts=[ArtifactRef(name="frame.jpg", mime="image/jpeg")],
        idempotency_key="sdk-test",
    )


def test_create_task_uploads_and_waits_for_result() -> None:
    state = {"task_id": "task-1", "polls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/tasks":
            return httpx.Response(
                201,
                json={
                    "task_id": "task-1",
                    "status": "uploading",
                    "task_type": "vision.detect.v1",
                    "expires_at": 9999,
                    "attempt": 0,
                    "result_available": False,
                    "upload_url": "/v1/tasks/task-1/input",
                },
                request=request,
            )
        if request.method == "PUT":
            assert request.url.params["size_bytes"] == "3"
            assert request.content == b"jpg"
            return httpx.Response(
                200,
                json={
                    "task_id": "task-1",
                    "status": "queued",
                    "task_type": "vision.detect.v1",
                    "expires_at": 9999,
                    "attempt": 0,
                    "result_available": False,
                },
                request=request,
            )
        if request.method == "GET" and request.url.path == "/v1/tasks/task-1":
            state["polls"] += 1
            status = "succeeded" if state["polls"] > 1 else "running"
            return httpx.Response(
                200,
                json={
                    "task_id": "task-1",
                    "status": status,
                    "task_type": "vision.detect.v1",
                    "expires_at": 9999,
                    "attempt": 1,
                    "result_available": status == "succeeded",
                },
                request=request,
            )
        if request.method == "GET" and request.url.path == "/v1/tasks/task-1/result":
            return httpx.Response(200, json={"schema_version": "vision.detect.v1"}, request=request)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = ModelCourierClient(
        "http://test",
        "device-token",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test"),
    )
    created = client.create_task(envelope(), b"jpg")
    assert created.status == "queued"
    assert client.wait_for_result("task-1", timeout=1, poll_interval=0) == {
        "schema_version": "vision.detect.v1"
    }


def test_wait_for_result_exposes_terminal_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "task_id": "task-2",
                "status": "failed",
                "task_type": "vision.detect.v1",
                "expires_at": 9999,
                "attempt": 3,
                "result_available": False,
                "error": {
                    "code": "provider_error",
                    "message": "model failed",
                    "retryable": False,
                    "request_id": "req-1",
                },
            },
            request=request,
        )

    client = ModelCourierClient(
        "http://test",
        "device-token",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test"),
    )
    with pytest.raises(TaskFailedError, match="model failed"):
        client.wait_for_result("task-2", timeout=1, poll_interval=0)


def test_client_retries_transient_server_error() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, text="busy", request=request)
        return httpx.Response(
            200,
            json={
                "task_id": "task-3",
                "status": "queued",
                "task_type": "vision.detect.v1",
                "expires_at": 9999,
                "attempt": 0,
                "result_available": False,
            },
            request=request,
        )

    client = ModelCourierClient(
        "http://test",
        "device-token",
        max_retries=1,
        retry_backoff=0,
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test"),
    )

    assert client.get_task("task-3").status == "queued"
    assert attempts == 2
