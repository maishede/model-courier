# ModelCourier MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a lightweight control plane and outbound Worker runtime that let Raspberry Pi and ESP32 devices submit versioned image/audio jobs to a local model without inbound connectivity to the GPU machine.

**Architecture:** A FastAPI control plane stores task metadata in SQLite WAL and artifacts on local disk. Devices use REST to create, upload, submit, and read jobs. A Python Worker polls over outbound HTTPS, claims leased jobs, resolves a Provider Factory, executes a local adapter, and publishes one valid result. The core contains contracts and a Mock Provider; real model integrations are optional packages.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, Uvicorn, SQLite (`sqlite3`), httpx, pytest, pytest-asyncio, Ruff. Optional providers use their own dependency sets and are never imported by the server core.

**Spec:** `docs/superpowers/specs/2026-09-18-model-courier-design.md`

## Global Constraints

- The control plane does not run model inference.
- SQLite stores metadata only; binary artifacts stay on the filesystem.
- The first device protocol is HTTPS REST with `/v1` versioning.
- The first Worker transport is HTTPS polling/long polling; MQTT and WebSocket are later adapters.
- Task types are `audio.transcribe.v1` and `vision.detect.v1`.
- Provider implementations are optional plugins; the core cannot import Faster-Whisper, FunASR, Ultralytics, Ollama, or their model weights.
- Every lease-sensitive request checks `owner_id`, `worker_id`, `lease_generation`, `lease_token`, and the server-side deadline.
- The system provides at-least-once execution and at-most-one published result; it does not promise model execution exactly once.
- Default task TTL is 24 hours, default lease is 10 minutes, heartbeat interval is 30 seconds, and a task has at most 3 attempts.
- Default limits are 8 MiB per image, 32 MiB per audio file, 120 seconds audio duration, 16 MiB result size, 10 active tasks per device, 100 active tasks per instance, and 10 GiB artifact budget.
- Inputs are deleted after successful publication; failed, expired, and cancelled inputs are retained for at most 24 hours; results are retained for 7 days.
- No secrets, file paths, raw inputs, or full prompts appear in ordinary logs.

---

### Task 1: Bootstrap the Python packages and versioned contracts

**Files:**
- Create: `pyproject.toml`
- Create: `src/model_courier/__init__.py`
- Create: `src/model_courier/contracts/__init__.py`
- Create: `src/model_courier/contracts/envelope.py`
- Create: `src/model_courier/contracts/capabilities.py`
- Create: `src/model_courier/contracts/results.py`
- Create: `src/model_courier/contracts/errors.py`
- Create: `tests/contracts/test_contracts.py`

**Interfaces:**
- Produces `TaskEnvelope`, `ArtifactRef`, `TaskRequirements`, `CapabilityManifest`, `ProviderResult`, and `ErrorEnvelope` as Pydantic models.
- `TaskEnvelope` fields: `protocol_version`, `task_type`, `input_artifacts`, `options`, `requires`, `idempotency_key`, `ttl_seconds`.
- `CapabilityManifest` fields: `capability_id`, `task_type`, `provider`, `models`, `formats`, `max_concurrency`.
- `ProviderResult` fields: `schema_version`, `json`, `artifacts`, `result_digest`.

- [ ] **Step 1: Write contract tests** for valid `audio.transcribe.v1` and `vision.detect.v1` envelopes, rejection of unknown task types, required `protocol_version`, bounded TTL, and canonical result fields.
- [ ] **Step 2: Run the contract tests** with `python -m pytest tests/contracts/test_contracts.py -q`; expect failure because the package does not exist.
- [ ] **Step 3: Implement the Pydantic models** with strict extra-field handling for server-owned fields, normalized MIME types, and a canonical JSON digest helper using sorted keys and UTF-8 encoding.
- [ ] **Step 4: Run the contract tests** and add tests for error serialization without secret or filesystem fields.
- [ ] **Step 5: Commit** with `git add pyproject.toml src/model_courier tests/contracts && git commit -m "feat: add versioned task contracts"`.

### Task 2: Implement SQLite metadata and lease-safe task transitions

**Files:**
- Create: `src/model_courier/server/__init__.py`
- Create: `src/model_courier/server/db.py`
- Create: `src/model_courier/server/repository.py`
- Create: `src/model_courier/server/migrations/001_initial.sql`
- Create: `tests/server/test_repository.py`

**Interfaces:**
- `Database.open(path: Path) -> Database`
- `Database.initialize() -> None`
- `TaskRepository.create_uploading(...) -> TaskRecord`
- `TaskRepository.finalize_and_enqueue(task_id, input_artifact, request_digest) -> TaskRecord`
- `TaskRepository.claim_next(worker_id, capabilities, now) -> Lease | None`
- `TaskRepository.start(lease) -> TaskRecord`
- `TaskRepository.heartbeat(lease, now) -> TaskRecord`
- `TaskRepository.requeue_expired(now) -> int`
- `TaskRepository.fail(lease, error, retryable, now) -> TaskRecord`
- `TaskRepository.publish_result(lease, result_manifest, now) -> TaskRecord`
- `TaskRepository.cancel(owner_id, task_id, now) -> TaskRecord`

- [ ] **Step 1: Write repository tests** for uploading-to-queued transition, capability matching, one-winner concurrent claims, lease generation changes, old-lease 409 behavior, retry backoff, task deadline, cancellation, and idempotency digest conflicts.
- [ ] **Step 2: Run `python -m pytest tests/server/test_repository.py -q`** and verify failures identify missing repository methods rather than test setup errors.
- [ ] **Step 3: Add the SQLite schema** for owners, devices, workers, tasks, artifacts, task_events, and idempotency uniqueness; enable foreign keys, WAL, busy timeout, and indexes for status, deadline, and capability matching.
- [ ] **Step 4: Implement short transactional claims** using `BEGIN IMMEDIATE`, server time, `lease_generation`, a hashed random `lease_token`, and conditional updates for every lease-sensitive transition.
- [ ] **Step 5: Implement requeue and cleanup queries** so expired leases return to `queued` only before the execution deadline and attempts remain bounded.
- [ ] **Step 6: Run repository tests**, including a two-thread claim race, and commit with `git add src/model_courier/server tests/server && git commit -m "feat: add lease-safe sqlite task repository"`.

### Task 3: Add artifact storage, credentials, quotas, and cleanup

**Files:**
- Create: `src/model_courier/server/storage.py`
- Create: `src/model_courier/server/auth.py`
- Create: `src/model_courier/server/quotas.py`
- Create: `src/model_courier/server/cleanup.py`
- Create: `tests/server/test_storage.py`
- Create: `tests/server/test_auth_and_quotas.py`

**Interfaces:**
- `ArtifactStore.begin(task_id, kind, declared_size, mime) -> UploadHandle`
- `ArtifactStore.write_stream(handle, chunks) -> ArtifactRef`
- `ArtifactStore.publish_result(task_id, lease_generation, files) -> list[ArtifactRef]`
- `ArtifactStore.delete_expired(now) -> CleanupReport`
- `TokenStore.issue(kind, subject_id) -> PlainToken`
- `TokenStore.authenticate(kind, bearer) -> Principal`
- `QuotaManager.reserve(owner_id, task_id, bytes) -> Reservation`

- [ ] **Step 1: Write storage tests** for streamed writes, maximum size rejection, SHA-256 verification, atomic rename, orphan detection, and result publication from a temporary directory.
- [ ] **Step 2: Write auth/quota tests** for hashed token storage, revoked tokens, device/Worker scope isolation, per-device task limits, global active-task limits, and the 5 GiB low-disk rejection threshold.
- [ ] **Step 3: Implement the filesystem layout** under a configurable root with `incoming/`, `staging/`, `results/`, and `orphaned/`; never store binary data in SQLite or base64 in JSON.
- [ ] **Step 4: Implement bearer authentication** using random URL-safe tokens stored only as SHA-256 hashes, with device and Worker principals resolved from the token rather than request fields.
- [ ] **Step 5: Implement reservation accounting and a cleanup pass** that is safe to rerun, protects active leases, removes expired files, and reports discrepancies for repair.
- [ ] **Step 6: Run storage and quota tests**, then commit with `git add src/model_courier/server tests/server && git commit -m "feat: add artifact storage auth and quotas"`.

### Task 4: Expose the device and Worker REST API

**Files:**
- Create: `src/model_courier/server/config.py`
- Create: `src/model_courier/server/dependencies.py`
- Create: `src/model_courier/server/app.py`
- Create: `src/model_courier/server/routes_devices.py`
- Create: `src/model_courier/server/routes_workers.py`
- Create: `tests/api/test_device_api.py`
- Create: `tests/api/test_worker_api.py`

**Interfaces:**
- Device routes: `POST /v1/tasks`, `PUT /v1/tasks/{task_id}/input`, `POST /v1/tasks/{task_id}/submit`, `GET /v1/tasks/{task_id}`, `GET /v1/tasks/{task_id}/result`, `POST /v1/tasks/{task_id}/cancel`.
- Worker routes: `POST /v1/workers/register`, `POST /v1/workers/poll`, `POST /v1/workers/tasks/{task_id}/start`, `POST /v1/workers/tasks/{task_id}/heartbeat`, `GET /v1/workers/tasks/{task_id}/input`, `PUT /v1/workers/tasks/{task_id}/result`, `POST /v1/workers/tasks/{task_id}/fail`.
- Errors serialize as `{code, message, retryable, request_id}` with status codes specified in the design spec.

- [ ] **Step 1: Write API tests** for device authentication, two-step upload/finalize, status visibility, result 409/410 behavior, cancellation, Worker capability registration, long poll 204, claim/start/heartbeat, old lease 409, and cross-principal 404/403 behavior.
- [ ] **Step 2: Run the API tests** and verify the failures occur at missing routes or repository wiring.
- [ ] **Step 3: Implement FastAPI dependencies** for configuration, SQLite connection lifecycle, principal extraction, request IDs, and error mapping.
- [ ] **Step 4: Implement streaming input upload** with declared size and MIME validation, quota reservation, digest calculation, atomic artifact publication, and the `uploading → queued` transition only after explicit submit.
- [ ] **Step 5: Implement Worker routes** with long-poll timeout capped at 25 seconds and no open SQLite transaction while waiting.
- [ ] **Step 6: Run API tests and a local Uvicorn smoke test**, then commit with `git add src/model_courier/server tests/api && git commit -m "feat: expose device and worker rest api"`.

### Task 5: Implement Provider contracts, Factory, and Mock Worker execution

**Files:**
- Create: `src/model_courier/worker/__init__.py`
- Create: `src/model_courier/worker/provider.py`
- Create: `src/model_courier/worker/factory.py`
- Create: `src/model_courier/worker/runtime.py`
- Create: `src/model_courier/providers/mock.py`
- Create: `tests/worker/test_factory.py`
- Create: `tests/worker/test_runtime.py`

**Interfaces:**
- `Provider.describe() -> CapabilityManifest`
- `Provider.validate(task: TaskEnvelope) -> None`
- `Provider.execute(task: TaskEnvelope, context: ExecutionContext) -> ProviderResult`
- `Provider.close() -> None`
- `ProviderFactory.discover() -> list[ProviderRegistration]`
- `ProviderFactory.create(capability_id, config) -> Provider`
- `WorkerRuntime.run_once() -> RunOutcome`
- `WorkerRuntime.run_forever(stop_event) -> None`

- [ ] **Step 1: Write provider tests** for entry-point discovery, local allowlisting, capability matching, hard provider/model requirements, and rejection of task-provided import paths or model URLs.
- [ ] **Step 2: Write runtime tests** using a Mock Provider for successful execution, progress/heartbeat callback, retryable provider error, non-retryable validation error, cancellation, timeout, and result digest.
- [ ] **Step 3: Implement the Provider Protocol and Factory** with explicit task-type registrations and a built-in Mock Provider; the server package must not import `model_courier.worker`.
- [ ] **Step 4: Implement Worker polling and lease tokens** using httpx, start/heartbeat/fail/result calls, exponential backoff, and server deadlines.
- [ ] **Step 5: Run the Worker tests** and commit with `git add src/model_courier/worker src/model_courier/providers tests/worker && git commit -m "feat: add provider factory and mock worker"`.

### Task 6: Add process isolation and provider package contracts

**Files:**
- Create: `src/model_courier/worker/process_runner.py`
- Create: `packages/provider-faster-whisper/pyproject.toml`
- Create: `packages/provider-faster-whisper/src/model_courier_provider_faster_whisper/provider.py`
- Create: `packages/provider-funasr/pyproject.toml`
- Create: `packages/provider-funasr/src/model_courier_provider_funasr/provider.py`
- Create: `packages/provider-ultralytics/pyproject.toml`
- Create: `packages/provider-ultralytics/src/model_courier_provider_ultralytics/provider.py`
- Create: `tests/providers/test_optional_provider_contracts.py`

**Interfaces:**
- `ProcessRunner.execute(provider_config, task, timeout, cancel_event) -> ProviderResult`
- Each optional package exports an entry point under `model_courier.providers` and implements the core Provider interface.
- `FasterWhisperProvider` maps output to `audio.transcribe.v1` text, language, and optional segments.
- `FunASRProvider` maps output to the same schema without exposing FunASR internals.
- `UltralyticsProvider` maps detections to `vision.detect.v1` labels, confidence, and pixel `xyxy` boxes.

- [ ] **Step 1: Write contract tests** with fake model objects and optional-import guards; tests must run without downloading weights or requiring a GPU.
- [ ] **Step 2: Implement subprocess execution** so model inference cannot block heartbeats; terminate after the execution deadline and preserve only normalized errors.
- [ ] **Step 3: Implement the Faster-Whisper, FunASR, and Ultralytics adapters** behind optional dependencies, with configuration limited to local model identifiers and runtime settings.
- [ ] **Step 4: Add provider metadata** documenting each dependency license, model-license review requirement, supported input formats, and CPU/GPU expectations.
- [ ] **Step 5: Run provider contract tests without optional dependencies**, then run available CPU smoke tests; commit with `git add src/model_courier/worker packages tests/providers && git commit -m "feat: add optional provider adapters"`.

### Task 7: Add SDK examples, deployment, and operator documentation

**Files:**
- Create: `packages/sdk-python/pyproject.toml`
- Create: `packages/sdk-python/src/model_courier_sdk/client.py`
- Create: `examples/raspberry-pi-submit-image.py`
- Create: `examples/esp32-rest-request.json`
- Create: `examples/worker-config.example.toml`
- Create: `deploy/Dockerfile`
- Create: `deploy/Caddyfile.example`
- Create: `deploy/systemd/model-courier-worker.service.example`
- Create: `README.md`
- Create: `docs/protocol.md`
- Create: `docs/provider-development.md`
- Create: `tests/sdk/test_client.py`

**Interfaces:**
- `ModelCourierClient.create_task(task_type, options, idempotency_key, ttl_seconds) -> TaskHandle`
- `ModelCourierClient.upload_input(handle, stream, mime, size) -> None`
- `ModelCourierClient.submit(handle) -> TaskHandle`
- `ModelCourierClient.wait(handle, timeout) -> TaskResult`

- [ ] **Step 1: Write SDK tests** for create/upload/submit/status/result mapping, retry after 503/429, and preservation of idempotency keys.
- [ ] **Step 2: Implement the small Python SDK** using httpx; keep it independent from Provider packages and usable on Raspberry Pi.
- [ ] **Step 3: Add device examples** showing an ESP32-compatible REST request shape and a Raspberry Pi image submission without embedding server secrets in source.
- [ ] **Step 4: Add deployment files** for a single API process, persistent SQLite/artifact volume, proxy limits, health checks, and Worker configuration.
- [ ] **Step 5: Document the task envelope, lifecycle, error codes, Provider entry point, token bootstrap, cleanup policy, and 2C2G resource limits.
- [ ] **Step 6: Run SDK tests and a local Docker smoke test**, then commit with `git add packages/sdk-python examples deploy README.md docs tests/sdk && git commit -m "docs: add sdk and deployment workflow"`.

### Task 8: Run end-to-end reliability verification and prepare the first release

**Files:**
- Create: `tests/e2e/test_mock_pipeline.py`
- Create: `tests/e2e/test_recovery.py`
- Create: `.github/workflows/ci.yml`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-09-18-model-courier-design.md`

- [ ] **Step 1: Write the end-to-end tests** for a mock device upload, offline Worker queueing, Worker recovery, duplicate claim race, expired lease, cancelled task, result cleanup, disk quota rejection, and provider swap without changing the device request.
- [ ] **Step 2: Run the full test suite** with `python -m pytest -q`; fix failures without weakening assertions.
- [ ] **Step 3: Run Ruff and type checks** with `ruff check .` and `python -m compileall src packages`; resolve all reported errors.
- [ ] **Step 4: Run a two-process local smoke test** with Uvicorn and the Worker runtime, then record observed upload, queue, result, and cleanup behavior in the README.
- [ ] **Step 5: Run a resource test** with the configured artifact limits and document actual memory, disk, and throughput observations instead of claiming unmeasured capacity.
- [ ] **Step 6: Update the design spec with any measured deviations, tag the first release as `v0.1.0`, and commit with `git add .github tests README.md docs && git commit -m "test: verify ModelCourier MVP pipeline"`.

## Plan self-review

- Spec coverage: contracts (Task 1), leases and retries (Task 2), artifact retention and quotas (Task 3), REST protocol (Task 4), Provider Factory (Task 5), process isolation and optional adapters (Task 6), SDK/deployment (Task 7), and end-to-end/resource verification (Task 8).
- Placeholder scan: no unresolved placeholder steps are used; every task names files, interfaces, tests, commands, and expected behavior.
- Type consistency: `TaskEnvelope`, `CapabilityManifest`, `ProviderResult`, `Provider`, `ProviderFactory`, `WorkerRuntime`, `ArtifactStore`, and `TaskRepository` names are consistent across tasks.
- The plan deliberately keeps model weights and heavy dependencies out of the core server image; real provider validation remains optional and must be backed by available local hardware.
