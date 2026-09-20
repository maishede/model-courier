"""Configured HTTP model adapter for user-managed local services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import Field, field_validator

from model_courier.contracts import ProviderResult, TaskEnvelope, canonical_json_digest
from model_courier.worker.provider import ProviderError

from .models import AgentModel


class HttpAdapterConfig(AgentModel):
    base_url: str = Field(min_length=1, max_length=2048)
    path: str = Field(default="/", min_length=1, max_length=512)
    method: str = Field(default="POST", pattern="^(POST|PUT)$")
    input_field: str = Field(default="file", min_length=1, max_length=128)
    model_field: str | None = Field(default=None, max_length=128)
    model_name: str | None = Field(default=None, max_length=256)
    response_path: str = Field(default="text", min_length=1, max_length=256)
    timeout: float = Field(default=60.0, gt=0, le=600)
    max_response_bytes: int = Field(default=32 * 1024 * 1024, ge=1, le=64 * 1024 * 1024)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an absolute HTTP URL")
        return value.rstrip("/")

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("path must start with '/'")
        return value


@dataclass(frozen=True)
class CheckResult:
    available: bool
    status_code: int | None = None
    error_code: str | None = None


class HttpAdapterError(ProviderError):
    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(code, f"{code}: {message}", retryable)


class HttpModelAdapter:
    def __init__(self, config: HttpAdapterConfig, client: httpx.Client | None = None) -> None:
        self.config = config
        self.url = f"{config.base_url}{config.path}"
        self.client = client or httpx.Client(base_url=config.base_url)

    def check(self) -> CheckResult:
        try:
            response = self.client.get(self.url, timeout=self.config.timeout)
        except httpx.TimeoutException:
            return CheckResult(False, error_code="timeout")
        except httpx.HTTPError:
            return CheckResult(False, error_code="connection_error")
        return CheckResult(
            response.is_success,
            response.status_code,
            None if response.is_success else "http_error",
        )

    def execute(self, task: TaskEnvelope, input_bytes: bytes) -> ProviderResult:
        data: dict[str, str] = {}
        if self.config.model_field and self.config.model_name:
            data[self.config.model_field] = self.config.model_name
        files = {
            self.config.input_field: (
                task.input_artifacts[0].name,
                input_bytes,
                task.input_artifacts[0].mime,
            )
        }
        try:
            response = self.client.request(
                self.config.method,
                self.url,
                data=data,
                files=files,
                timeout=self.config.timeout,
            )
        except httpx.TimeoutException as exc:
            raise HttpAdapterError("timeout", "HTTP request timed out", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise HttpAdapterError(
                "connection_error", "HTTP request failed", retryable=True
            ) from exc
        if len(response.content) > self.config.max_response_bytes:
            raise HttpAdapterError("response_too_large", "HTTP response exceeded the size limit")
        if not response.is_success:
            raise HttpAdapterError(
                "http_error",
                f"HTTP service returned status {response.status_code}",
                retryable=response.status_code >= 500,
            )
        try:
            document = response.json()
        except ValueError as exc:
            raise HttpAdapterError("invalid_json", "HTTP service returned invalid JSON") from exc
        value = _extract(document, self.config.response_path)
        if value is _MISSING:
            raise HttpAdapterError(
                "response_field_missing", "configured response field was not found"
            )
        payload: dict[str, Any] = (
            value
            if isinstance(value, dict)
            else {self.config.response_path.rsplit(".", 1)[-1]: value}
        )
        return ProviderResult(
            schema_version=task.task_type,
            json=payload,
            result_digest=canonical_json_digest(payload),
        )


_MISSING = object()


def _extract(document: Any, path: str) -> Any:
    value = document
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return _MISSING
        value = value[part]
    return value
