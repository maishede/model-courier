"""Worker Provider implementations backed by persisted Agent bindings."""

from __future__ import annotations

import time
from pathlib import Path

from model_courier.contracts import (
    CapabilityManifest,
    ProviderResult,
    TaskEnvelope,
    canonical_json_digest,
)
from model_courier.worker.provider import ExecutionContext, ProviderError

from .http_adapter import HttpAdapterConfig, HttpModelAdapter
from .models import ModelBinding
from .python_bridge import BridgeError, PythonBridge


class BindingProvider:
    """Adapt one user-approved model binding to the existing Worker protocol."""

    def __init__(self, binding: ModelBinding) -> None:
        self.binding = binding

    def describe(self) -> CapabilityManifest:
        formats = self.binding.execution.config.get("formats")
        if not isinstance(formats, list) or not all(isinstance(item, str) for item in formats):
            formats = _default_formats(self.binding.task_type)
        return CapabilityManifest(
            capability_id=self.binding.binding_id,
            task_type=self.binding.task_type,
            provider=self.binding.execution.adapter_id,
            service_id=self.binding.service_id,
            models=[self.binding.model_name],
            formats=formats,
        )

    def validate(self, task: TaskEnvelope) -> None:
        if task.task_type != self.binding.task_type:
            raise ProviderError("unsupported_task", "binding does not support this task type")
        if self.binding.execution.kind == "http":
            try:
                HttpAdapterConfig.model_validate(self.binding.execution.config)
            except ValueError as exc:
                raise ProviderError(
                    "invalid_configuration", "HTTP configuration is invalid"
                ) from exc
            return
        if self.binding.execution.kind == "python":
            executable = self.binding.execution.config.get("executable")
            if not isinstance(executable, str) or not Path(executable).is_file():
                raise ProviderError("executable_missing", "Python executable was not found")
            arguments = self.binding.execution.config.get("arguments", [])
            if not isinstance(arguments, list) or not all(
                isinstance(argument, str) for argument in arguments
            ):
                raise ProviderError("invalid_configuration", "Python arguments must be strings")
            return
        raise ProviderError("managed_http_pending", "managed HTTP is not available yet")

    def execute(self, task: TaskEnvelope, context: ExecutionContext) -> ProviderResult:
        if not context.input_paths:
            raise ProviderError("input_missing", "task input is missing")
        if self.binding.execution.kind == "http":
            adapter = HttpModelAdapter(
                HttpAdapterConfig.model_validate(self.binding.execution.config)
            )
            try:
                return adapter.execute(task, context.input_paths[0].read_bytes())
            finally:
                adapter.close()
        if self.binding.execution.kind == "python":
            return self._execute_python(task, context)
        raise ProviderError("managed_http_pending", "managed HTTP is not available yet")

    def close(self) -> None:
        return None

    def _execute_python(self, task: TaskEnvelope, context: ExecutionContext) -> ProviderResult:
        config = self.binding.execution.config
        executable = config.get("executable")
        arguments = config.get("arguments", [])
        if not isinstance(executable, str) or not isinstance(arguments, list):
            raise ProviderError("invalid_configuration", "Python bridge configuration is invalid")
        request = {
            "request_id": context.task_id,
            "operation": "execute",
            "task_type": task.task_type,
            "model": self.binding.model_name,
            "input_path": str(context.input_paths[0]),
            "options": task.options,
        }
        try:
            response = PythonBridge(
                Path(executable),
                arguments,
                timeout=max(0.1, context.deadline - time.time()),
            ).run(request)
        except BridgeError as exc:
            raise ProviderError(
                exc.code, str(exc), retryable=exc.code in {"bridge_timeout"}
            ) from exc
        payload = response.get("json", response.get("result"))
        if not isinstance(payload, dict):
            raise ProviderError("invalid_result", "Python adapter must return a JSON object")
        return ProviderResult(
            schema_version=task.task_type,
            json=payload,
            result_digest=canonical_json_digest(payload),
        )


def _default_formats(task_type: str) -> list[str]:
    if task_type == "vision.detect.v1":
        return ["image/jpeg", "image/png"]
    return ["audio/wav", "audio/mpeg"]
