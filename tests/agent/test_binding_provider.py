from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from model_courier.agent.models import ExecutionProfile, ModelBinding
from model_courier.agent.providers import BindingProvider
from model_courier.agent.store import AgentStore
from model_courier.agent.worker import build_binding_factory
from model_courier.contracts import ArtifactRef, TaskEnvelope
from model_courier.worker.provider import ExecutionContext, ProviderError


def http_binding() -> ModelBinding:
    return ModelBinding(
        binding_id="local-http",
        display_name="Local HTTP",
        service_id="desktop-gpu-1",
        execution=ExecutionProfile(
            profile_id="http-local",
            kind="http",
            adapter_id="http.transcribe.v1",
            config={
                "base_url": "http://127.0.0.1:8000",
                "path": "/transcribe",
                "response_path": "result.text",
            },
        ),
        model_name="paraformer",
        task_type="audio.transcribe.v1",
        enabled=True,
    )


def test_binding_provider_exposes_stable_capability() -> None:
    provider = BindingProvider(http_binding())

    capability = provider.describe()

    assert capability.capability_id == "local-http"
    assert capability.service_id == "desktop-gpu-1"
    assert capability.provider == "http.transcribe.v1"
    assert capability.models == ["paraformer"]


def test_python_binding_provider_rejects_missing_executable() -> None:
    binding = http_binding().model_copy(
        update={
            "execution": ExecutionProfile(
                profile_id="python-local",
                kind="python",
                adapter_id="python.bridge.v1",
                config={"executable": str(Path("missing-python"))},
            )
        }
    )

    provider = BindingProvider(binding)

    task = TaskEnvelope(
        task_type="audio.transcribe.v1",
        input_artifacts=[ArtifactRef(name="sample.wav", mime="audio/wav")],
        idempotency_key="provider-test",
    )
    try:
        provider.validate(task)
    except Exception as error:
        assert getattr(error, "code", None) == "executable_missing"
    else:
        raise AssertionError("missing executable should fail validation")


def test_binding_factory_registers_only_enabled_bindings(tmp_path: Path) -> None:
    store = AgentStore(tmp_path / "agent.sqlite3")
    store.save_binding(http_binding())
    store.save_binding(
        http_binding().model_copy(update={"binding_id": "disabled", "enabled": False})
    )

    factory = build_binding_factory(store)

    assert [capability.capability_id for capability in factory.capabilities()] == ["local-http"]


def test_python_provider_uses_remaining_execution_deadline(tmp_path: Path) -> None:
    script = tmp_path / "slow_bridge.py"
    script.write_text(
        "import json, sys, time\n"
        "request = json.loads(sys.stdin.readline())\n"
        "time.sleep(0.2)\n"
        "print(json.dumps({'request_id': request['request_id'], "
        "'json': {'text': 'ok'}}), flush=True)\n",
        encoding="utf-8",
    )
    binding = http_binding().model_copy(
        update={
            "execution": ExecutionProfile(
                profile_id="python-local",
                kind="python",
                adapter_id="python.bridge.v1",
                config={"executable": sys.executable, "arguments": [str(script)]},
            )
        }
    )
    input_path = tmp_path / "sample.wav"
    input_path.write_bytes(b"sample")
    task = TaskEnvelope(
        task_type="audio.transcribe.v1",
        input_artifacts=[ArtifactRef(name="sample.wav", mime="audio/wav")],
        idempotency_key="deadline-test",
    )

    with pytest.raises(ProviderError, match="bridge_timeout"):
        BindingProvider(binding).execute(
            task,
            ExecutionContext(
                task_id="deadline-test",
                deadline=time.time() + 0.05,
                input_paths=[input_path],
            ),
        )
