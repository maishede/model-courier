from __future__ import annotations

import pytest

from model_courier.providers.mock import mock_audio_registration, mock_registration
from model_courier.worker.factory import ProviderFactory


def test_factory_registers_and_creates_allowlisted_provider() -> None:
    factory = ProviderFactory(allowlist={"mock:generic"})
    factory.register(mock_registration())
    factory.register(mock_audio_registration())

    assert [item.capability_id for item in factory.capabilities()] == ["mock:generic"]
    provider = factory.create("mock:generic")
    assert provider.describe().provider == "mock"


def test_factory_requires_registered_capability() -> None:
    factory = ProviderFactory()
    with pytest.raises(KeyError, match="not registered"):
        factory.create("missing")
