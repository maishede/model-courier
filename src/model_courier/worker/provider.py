"""Provider interfaces and execution context."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from model_courier.contracts import CapabilityManifest, ProviderResult, TaskEnvelope


class ProviderError(RuntimeError):
    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass
class ExecutionContext:
    task_id: str
    deadline: float
    input_paths: list[Path] = field(default_factory=list)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    progress_callback: Callable[[float, str | None], None] | None = None

    def report_progress(self, progress: float, message: str | None = None) -> None:
        if self.cancel_event.is_set():
            raise ProviderError("execution_cancelled", "execution was cancelled", retryable=True)
        if self.progress_callback is not None:
            self.progress_callback(max(0.0, min(1.0, progress)), message)


class Provider(Protocol):
    def describe(self) -> CapabilityManifest: ...

    def validate(self, task: TaskEnvelope) -> None: ...

    def execute(self, task: TaskEnvelope, context: ExecutionContext) -> ProviderResult: ...

    def close(self) -> None: ...
