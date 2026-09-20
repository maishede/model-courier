from __future__ import annotations

import threading

import pytest

from model_courier.agent.runtime import AgentRuntimeController, RuntimeUnavailable


class FakeWorkerRuntime:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.stopped = threading.Event()

    def run_forever(self, stop_event: threading.Event) -> None:
        self.started.set()
        stop_event.wait()
        self.stopped.set()


def test_runtime_without_worker_is_unavailable() -> None:
    controller = AgentRuntimeController(None)

    assert controller.status() == "unconfigured"
    with pytest.raises(RuntimeUnavailable):
        controller.start()


def test_runtime_start_is_idempotent_and_stop_ends_worker() -> None:
    worker = FakeWorkerRuntime()
    controller = AgentRuntimeController(worker)

    controller.start()
    controller.start()

    assert worker.started.wait(1)
    assert controller.status() == "running"
    controller.stop()

    assert worker.stopped.wait(1)
    assert controller.status() == "stopped"


def test_runtime_reports_worker_failure() -> None:
    class FailingWorker:
        def run_forever(self, stop_event: threading.Event) -> None:
            raise RuntimeError("platform unavailable")

    controller = AgentRuntimeController(FailingWorker())
    controller.start()

    for _ in range(100):
        if controller.status() == "error":
            break
        threading.Event().wait(0.01)

    assert controller.status() == "error"


def test_runtime_reports_stopping_state_when_restarted_before_old_worker_exits() -> None:
    class SlowStoppingWorker:
        def __init__(self) -> None:
            self.started = threading.Event()
            self.release = threading.Event()

        def run_forever(self, stop_event: threading.Event) -> None:
            self.started.set()
            self.release.wait()

    worker = SlowStoppingWorker()
    controller = AgentRuntimeController(worker)
    controller.start()
    assert worker.started.wait(1)
    controller.stop()

    with pytest.raises(RuntimeError, match="still stopping"):
        controller.start()

    worker.release.set()
