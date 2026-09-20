"""Connect persisted Agent bindings to the existing outbound Worker runtime."""

from __future__ import annotations

import threading

from model_courier.worker.factory import ProviderFactory, ProviderRegistration
from model_courier.worker.runtime import WorkerConfig, WorkerRuntime

from .providers import BindingProvider
from .store import AgentStore


def build_binding_factory(store: AgentStore) -> ProviderFactory:
    factory = ProviderFactory()
    for binding in store.list_bindings():
        if not binding.enabled or not store.is_verified(binding.binding_id):
            continue
        provider = BindingProvider(binding)
        factory.register(
            ProviderRegistration(
                provider.describe(),
                lambda _config, selected=binding: BindingProvider(selected),
            )
        )
    return factory


class AgentWorkerLoop:
    """Build a fresh capability set and run the public Worker loop."""

    def __init__(
        self,
        store: AgentStore,
        base_url: str,
        token: str,
        *,
        poll_seconds: int = 25,
        request_timeout: float = 30.0,
        execution_timeout: float = 600.0,
        heartbeat_seconds: float = 30.0,
        execution_lock: threading.Lock | None = None,
    ) -> None:
        self.store = store
        self.config = WorkerConfig(
            base_url=base_url,
            token=token,
            poll_seconds=poll_seconds,
            request_timeout=request_timeout,
            execution_timeout=execution_timeout,
            heartbeat_seconds=heartbeat_seconds,
        )
        self.execution_lock = execution_lock or threading.Lock()

    def run_forever(self, stop_event: threading.Event) -> None:
        runtime = WorkerRuntime(self.config, build_binding_factory(self.store))
        runtime.register()
        while not stop_event.is_set():
            with self.execution_lock:
                outcome = runtime.run_once()
            if outcome.status == "idle":
                stop_event.wait(min(1.0, self.config.poll_seconds))
