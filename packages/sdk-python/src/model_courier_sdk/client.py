"""Small synchronous client for the device-facing ModelCourier API."""

from __future__ import annotations

import mimetypes
import time
from pathlib import Path
from typing import BinaryIO

import httpx
from pydantic import BaseModel, ConfigDict

from model_courier.contracts import ErrorEnvelope, TaskEnvelope


class ModelCourierError(RuntimeError):
    """An HTTP or protocol error returned by the ModelCourier server."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"ModelCourier request failed ({status_code}): {detail}")
        self.status_code = status_code
        self.detail = detail


class TaskFailedError(ModelCourierError):
    """Raised by :meth:`ModelCourierClient.wait_for_result` for a terminal failure."""

    def __init__(self, task: TaskView) -> None:
        detail = task.error.message if task.error else f"task ended with status {task.status}"
        super().__init__(409, detail)
        self.task = task


class TaskView(BaseModel):
    """Server representation of a task returned by status endpoints."""

    model_config = ConfigDict(extra="ignore")

    task_id: str
    status: str
    task_type: str
    expires_at: float
    attempt: int
    result_available: bool
    result_expires_at: float | None = None
    upload_url: str | None = None
    error: ErrorEnvelope | None = None


Payload = bytes | bytearray | memoryview | Path | BinaryIO


class ModelCourierClient:
    """Use a device token to submit and observe one-input tasks.

    The current server API accepts one binary input per task. The input's name and
    MIME type are taken from the first ``TaskEnvelope.input_artifacts`` entry.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
        max_retries: int = 2,
        retry_backoff: float = 0.25,
    ) -> None:
        self._client = client or httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
        self._owns_client = client is None
        self._max_retries = max(0, max_retries)
        self._retry_backoff = max(0.0, retry_backoff)

    def __enter__(self) -> ModelCourierClient:
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def create_task(self, envelope: TaskEnvelope, payload: Payload) -> TaskView:
        """Create a task, upload its first input, and return its queued view."""

        if len(envelope.input_artifacts) != 1:
            raise ValueError("the MVP SDK accepts exactly one input artifact per task")
        artifact = envelope.input_artifacts[0]
        data, size = self._payload_and_size(payload)
        if artifact.size_bytes is not None and artifact.size_bytes != size:
            raise ValueError(
                f"input size ({size}) does not match artifact declaration ({artifact.size_bytes})"
            )
        response = self._request(
            "POST",
            "/v1/tasks",
            json=envelope.model_dump(mode="json"),
        )
        created = TaskView.model_validate(response.json())
        upload_url = created.upload_url or f"/v1/tasks/{created.task_id}/input"
        uploaded = self._request(
            "PUT",
            upload_url,
            params={
                "name": artifact.name,
                "mime": artifact.mime,
                "size_bytes": size,
            },
            content=data,
        )
        return TaskView.model_validate(uploaded.json())

    def get_task(self, task_id: str) -> TaskView:
        response = self._request("GET", f"/v1/tasks/{task_id}")
        return TaskView.model_validate(response.json())

    def get_result(self, task_id: str) -> dict:
        response = self._request("GET", f"/v1/tasks/{task_id}/result")
        return response.json()

    def cancel_task(self, task_id: str) -> TaskView:
        response = self._request("POST", f"/v1/tasks/{task_id}/cancel")
        return TaskView.model_validate(response.json())

    def wait_for_result(
        self,
        task_id: str,
        *,
        timeout: float = 300.0,
        poll_interval: float = 1.0,
    ) -> dict:
        """Poll until a task succeeds, fails, is cancelled, or reaches the timeout."""

        deadline = time.monotonic() + timeout
        while True:
            task = self.get_task(task_id)
            if task.status == "succeeded":
                return self.get_result(task_id)
            if task.status in {"failed", "cancelled"}:
                raise TaskFailedError(task)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"task {task_id} did not finish before timeout")
            time.sleep(min(max(poll_interval, 0.0), remaining))

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        response: httpx.Response | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.TransportError:
                if attempt >= self._max_retries:
                    raise
            else:
                if response.status_code not in {429, 500, 502, 503, 504}:
                    break
            if self._retry_backoff:
                time.sleep(self._retry_backoff * (2**attempt))
        assert response is not None
        if response.is_error:
            try:
                body = response.json()
                detail = body.get("detail", body)
            except ValueError:
                detail = response.text
            raise ModelCourierError(response.status_code, str(detail))
        return response

    @staticmethod
    def _payload_and_size(payload: Payload) -> tuple[Payload, int]:
        if isinstance(payload, (bytes, bytearray, memoryview)):
            return payload, len(payload)
        if isinstance(payload, Path):
            return payload.read_bytes(), payload.stat().st_size
        if hasattr(payload, "seek") and hasattr(payload, "tell"):
            position = payload.tell()
            payload.seek(0, 2)
            size = payload.tell()
            payload.seek(position)
            return payload, size - position
        raise TypeError("payload must be bytes, pathlib.Path, or a seekable binary file")


def guess_mime(path: str | Path) -> str:
    """Guess a file MIME type for callers constructing an ArtifactRef."""

    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"
