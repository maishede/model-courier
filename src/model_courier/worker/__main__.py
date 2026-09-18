"""Run a configured outbound ModelCourier Worker."""

from __future__ import annotations

import os
import signal
import threading

from model_courier.worker.factory import ProviderFactory
from model_courier.worker.runtime import WorkerConfig, WorkerRuntime


def main() -> None:
    factory = ProviderFactory()
    factory.discover()
    runtime = WorkerRuntime(
        WorkerConfig(
            base_url=os.environ["MODEL_COURIER_URL"],
            token=os.environ["MODEL_COURIER_WORKER_TOKEN"],
        ),
        factory,
    )
    stop_event = threading.Event()
    signal.signal(signal.SIGTERM, lambda _signum, _frame: stop_event.set())
    signal.signal(signal.SIGINT, lambda _signum, _frame: stop_event.set())
    runtime.run_forever(stop_event)


if __name__ == "__main__":
    main()
