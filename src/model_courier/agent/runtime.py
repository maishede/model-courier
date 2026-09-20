"""Lifecycle control for the outbound Worker owned by the desktop Agent."""

from __future__ import annotations

import threading
from typing import Protocol


class WorkerLoop(Protocol):
    def run_forever(self, stop_event: threading.Event) -> None: ...


class RuntimeUnavailable(RuntimeError):
    """Raised when the Agent has no platform Worker configured."""


class AgentRuntimeController:
    def __init__(self, worker: WorkerLoop | None) -> None:
        self._worker = worker
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._state = "unconfigured" if worker is None else "stopped"

    def status(self) -> str:
        with self._lock:
            return self._state

    def start(self) -> None:
        if self._worker is None:
            raise RuntimeUnavailable("platform Worker is not configured")
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                if self._stop_event.is_set():
                    raise RuntimeUnavailable("worker is still stopping")
                return
            self._stop_event = threading.Event()
            self._state = "starting"
            self._thread = threading.Thread(
                target=self._run,
                name="model-courier-worker",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            thread = self._thread
            if thread is None:
                if self._worker is not None:
                    self._state = "stopped"
                return
            self._stop_event.set()
        thread.join(timeout=2.0)
        with self._lock:
            if not thread.is_alive():
                self._state = "stopped"

    def _run(self) -> None:
        with self._lock:
            self._state = "running"
        try:
            self._worker.run_forever(self._stop_event)
        except Exception:
            with self._lock:
                self._state = "error"
            return
        with self._lock:
            self._state = "stopped"
