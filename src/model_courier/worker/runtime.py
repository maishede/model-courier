"""Outbound Worker loop for the versioned task protocol."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from model_courier.contracts import TaskEnvelope

from .factory import ProviderFactory
from .process_runner import ProcessRunner
from .provider import ProviderError


@dataclass(frozen=True)
class WorkerConfig:
    base_url: str
    token: str
    poll_seconds: int = 25
    request_timeout: float = 30.0


@dataclass(frozen=True)
class RunOutcome:
    status: str
    task_id: str | None = None
    message: str | None = None


class WorkerRuntime:
    def __init__(
        self,
        config: WorkerConfig,
        factory: ProviderFactory,
        client: httpx.Client | None = None,
        process_runner: ProcessRunner | None = None,
    ) -> None:
        self.config = config
        self.factory = factory
        self.process_runner = process_runner or ProcessRunner()
        self.client = client or httpx.Client(
            base_url=config.base_url,
            headers={"Authorization": f"Bearer {config.token}"},
            timeout=config.request_timeout,
        )

    def run_once(self) -> RunOutcome:
        response = self.client.post("/v1/workers/poll", json={"wait_seconds": 0})
        if response.status_code == 204:
            return RunOutcome("idle")
        response.raise_for_status()
        lease = response.json()
        task_id = lease["task_id"]
        task = TaskEnvelope.model_validate(lease["task"])
        lease_request = {
            "lease_generation": lease["lease_generation"],
            "lease_token": lease["lease_token"],
        }
        self.client.post(
            f"/v1/workers/tasks/{task_id}/start", json=lease_request
        ).raise_for_status()
        capability_id = lease["capability_id"]
        provider = self.factory.create(capability_id)
        try:
            provider.validate(task)
            with tempfile.TemporaryDirectory(prefix="model-courier-") as temp_dir:
                input_paths = self._download_inputs(task_id, Path(temp_dir))
                result = self.process_runner.execute(
                    provider,
                    task,
                    task_id,
                    input_paths,
                    self.config.request_timeout,
                )
            self.client.put(
                f"/v1/workers/tasks/{task_id}/result",
                params=lease_request,
                json=result.model_dump(mode="json", by_alias=True),
            ).raise_for_status()
            return RunOutcome("succeeded", task_id=task_id)
        except ProviderError as exc:
            error = {
                "code": exc.code,
                "message": str(exc),
                "retryable": exc.retryable,
                "request_id": f"worker-{task_id}",
            }
            self.client.post(
                f"/v1/workers/tasks/{task_id}/fail",
                json={**lease_request, "error": error},
            ).raise_for_status()
            return RunOutcome("failed", task_id=task_id, message=str(exc))
        except Exception as exc:
            error = {
                "code": "worker_execution_error",
                "message": str(exc)[:512],
                "retryable": True,
                "request_id": f"worker-{task_id}",
            }
            self.client.post(
                f"/v1/workers/tasks/{task_id}/fail",
                json={**lease_request, "error": error},
            ).raise_for_status()
            return RunOutcome("failed", task_id=task_id, message=str(exc))
        finally:
            provider.close()

    def run_forever(self, stop_event: Any) -> None:
        while not stop_event.is_set():
            outcome = self.run_once()
            if outcome.status == "idle":
                stop_event.wait(1.0)

    def _download_inputs(self, task_id: str, directory: Path) -> list[Path]:
        response = self.client.get(f"/v1/workers/tasks/{task_id}/input")
        response.raise_for_status()
        path = directory / "input-0"
        path.write_bytes(response.content)
        return [path]
