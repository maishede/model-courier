# ModelCourier

ModelCourier is an outbound AI task relay for Raspberry Pi, ESP32, and other
small devices. A device sends an HTTPS task to a small public control plane;
your local GPU Worker makes an outbound connection, runs a local model, and
returns the normalized result. The GPU machine never needs a public IP, port
forwarding, or an inbound tunnel.

The first release is intentionally single-owner and self-hosted. The control
plane uses FastAPI, SQLite WAL, and local artifact files, so a 2C2G / 40 GB
server does not need PostgreSQL, Redis, RabbitMQ, or a cloud inference API.
Heavy model dependencies stay in optional Worker-side packages.

## Repository layout

- `src/model_courier/contracts`: versioned task, capability, result, and error schemas.
- `src/model_courier/server`: public API, SQLite repository, leases, auth, quotas, and files.
- `src/model_courier/worker`: outbound polling runtime, Provider Factory, and process isolation.
- `packages/provider-*`: optional Faster-Whisper, FunASR, and Ultralytics adapters.
- `packages/sdk-python`: small device-side client for Python and Raspberry Pi.
- `docs/protocol.md`: REST contract and lifecycle.
- `docs/provider-development.md`: add another local model backend without changing the core.
- `deploy/`: single-process deployment and reverse-proxy examples.

## Run the control plane

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows: .venv\\Scripts\\activate
python -m pip install -e ".[test]"
uvicorn model_courier.server.app:create_app --factory --host 0.0.0.0 --port 8080
```

The default data directory is `./data`; it contains the SQLite database and
artifact files. Keep it on persistent storage and put a TLS reverse proxy in
front of Uvicorn. The API process should run with one worker when using the
small server profile.

For a first local token pair, use the server's database objects from a short
administrative script. Tokens are only shown once:

```python
from pathlib import Path
import time

from model_courier.server.app import create_app
from model_courier.server.config import ServerConfig
from model_courier.contracts import CapabilityManifest

app = create_app(ServerConfig.from_root(Path("data")))
state = app.state.model_courier
device_token = state.tokens.issue_device("owner-1", "pi-1", time.time())
worker_token = state.tokens.issue_worker(
    "owner-1",
    "gpu-1",
    [CapabilityManifest(
        capability_id="mock:generic",
        task_type="vision.detect.v1",
        provider="mock",
        models=["mock-1"],
        formats=["image/jpeg", "image/png"],
    )],
    time.time(),
)
print(device_token)
print(worker_token)
```

## Submit from a device

Install the SDK beside the core package during development:

```bash
python -m pip install -e packages/sdk-python
```

```python
from model_courier.contracts import ArtifactRef, TaskEnvelope
from model_courier_sdk import ModelCourierClient

task = TaskEnvelope(
    task_type="vision.detect.v1",
    input_artifacts=[ArtifactRef(name="frame.jpg", mime="image/jpeg")],
    idempotency_key="pi-1:camera:00042",
)

with ModelCourierClient("https://relay.example.com", "DEVICE_TOKEN") as client:
    view = client.create_task(task, b"...jpeg bytes...")
    result = client.wait_for_result(view.task_id, timeout=120)
    print(result)
```

For ESP32 or another non-Python device, send the same JSON envelope and upload
the bytes with `PUT /v1/tasks/{task_id}/input`; the exact request shape is in
`examples/esp32-rest-request.json` and `docs/protocol.md`.

## Run a Worker

The Worker is outbound-only. The built-in Mock Provider is useful for a first
end-to-end check:

```python
import os
import threading

from model_courier.providers.mock import mock_registration
from model_courier.worker.factory import ProviderFactory
from model_courier.worker.runtime import WorkerConfig, WorkerRuntime

factory = ProviderFactory()
factory.register(mock_registration())
runtime = WorkerRuntime(
    WorkerConfig("https://relay.example.com", os.environ["MODEL_COURIER_WORKER_TOKEN"]),
    factory,
)
runtime.run_forever(threading.Event())
```

Install a model adapter only on the GPU machine, for example:

```bash
python -m pip install -e "packages/provider-faster-whisper[runtime]"
```

Each adapter registers a capability through the `model_courier.providers`
entry-point group. Provider code is loaded in a killable child process and is
never imported by the public server. See `docs/provider-development.md` for a
custom adapter.

## Reliability and limits

Tasks are persisted before a Worker sees them. Leases use a generation and a
random token, so a late Worker cannot overwrite a newer attempt. The system
provides at-least-once execution and at-most-one published result; a model may
run again after a crash. Default task TTL is 24 hours, lease duration is 10
minutes, and a task has at most three attempts.

The default artifact budget is 10 GiB, with an 8 MiB image limit, a 32 MiB
audio limit, a 16 MiB JSON result limit, and a 5 GiB free-disk safety floor.
Successful inputs are removed after result publication; result retention is
seven days. These settings are code-level MVP defaults and should be measured
against the actual disk and traffic profile before exposing the service.

Run verification locally with:

```bash
python -m pytest -q
ruff check src tests packages
python -m compileall -q src packages
```

This project is an infrastructure component, not a hosted inference service.
You remain responsible for model licenses, uploaded data retention, TLS, token
rotation, and access control at the deployment boundary.
