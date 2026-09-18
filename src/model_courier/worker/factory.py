"""Provider discovery, allowlisting, and factory creation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any

from model_courier.contracts import CapabilityManifest

from .provider import Provider


@dataclass(frozen=True)
class ProviderRegistration:
    capability: CapabilityManifest
    factory: Callable[[dict[str, Any]], Provider]


class ProviderFactory:
    def __init__(self, allowlist: set[str] | None = None) -> None:
        self.allowlist = allowlist
        self._registrations: dict[str, ProviderRegistration] = {}

    def register(self, registration: ProviderRegistration) -> None:
        capability_id = registration.capability.capability_id
        if self.allowlist is not None and capability_id not in self.allowlist:
            return
        self._registrations[capability_id] = registration

    def discover(self) -> list[ProviderRegistration]:
        discovered: list[ProviderRegistration] = []
        for item in entry_points().select(group="model_courier.providers"):
            loaded = item.load()
            registration = loaded() if callable(loaded) else loaded
            if not isinstance(registration, ProviderRegistration):
                raise TypeError(f"entry point {item.name} did not return ProviderRegistration")
            self.register(registration)
            discovered.append(registration)
        return discovered

    def capabilities(self) -> list[CapabilityManifest]:
        return [registration.capability for registration in self._registrations.values()]

    def create(self, capability_id: str, config: dict[str, Any] | None = None) -> Provider:
        registration = self._registrations.get(capability_id)
        if registration is None:
            raise KeyError(f"provider capability is not registered: {capability_id}")
        return registration.factory(config or {})
