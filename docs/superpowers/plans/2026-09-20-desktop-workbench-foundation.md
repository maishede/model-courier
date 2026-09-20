# Desktop Workbench Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add the protocol and local execution foundations required for a Flutter desktop workbench that can connect to existing HTTP models or run models inside user-selected Conda/Python environments.

**Architecture:** Extend the existing versioned task protocol with a stable service identity and online presence. Add a Python `agent` package that owns local profiles, environment checks, JSONL Python bridges, and HTTP adapters; keep the existing public server and Worker compatible. Add a minimal Flutter desktop shell only after the Python contracts are testable, with a loopback Agent API boundary so Flutter never imports model code.

**Tech Stack:** Python 3.11, Pydantic v2, FastAPI, SQLite, httpx, pytest, Flutter/Dart desktop shell.

**Spec:** `docs/superpowers/specs/2026-09-20-desktop-workbench-design.md`

## Global Constraints

- The public control plane remains suitable for a 2C2G / 40 GB server and does not add Redis, RabbitMQ, PostgreSQL, or model dependencies.
- The default desktop target is Windows x64; the Agent must not require system Python for the GUI package.
- Model frameworks run only in user-selected environments or user-managed HTTP services; the public server never imports them.
- Remote tasks cannot supply executable commands, import paths, environment paths, target URLs, or arbitrary headers.
- Local API listens on loopback only and requires a per-session high-entropy credential.
- Secrets never appear in command lines, logs, diagnostics exports, or plaintext profile JSON.
- Existing protocol v1 clients and CLI Workers remain compatible when optional fields are absent.
- Every production behavior is introduced by a failing test first, followed by the smallest implementation.

## Review Focus

- A task that names `service_id` must not be claimed by a Worker that only matches the same provider/model; test explicit service routing and legacy routing together in Task 1.
- A stale Worker must stop advertising usable capabilities after the presence TTL; test the online cutoff and pause transition in Task 1.
- A Conda path containing spaces and a Python executable with arguments must be passed as an argument vector rather than shell text; test this in Task 2.
- Malformed, oversized, or secret-bearing JSONL bridge messages must fail closed and redact diagnostics; test this in Task 2.
- HTTP response extraction must reject a syntactically valid response with a missing result field and must enforce response size/time limits; test this in Task 3.

---

### Task 1: Stable service routing and Worker presence

**Files:**
- Modify: `src/model_courier/contracts/capabilities.py`
- Modify: `src/model_courier/server/repository.py`
- Modify: `src/model_courier/server/schemas.py`
- Modify: `src/model_courier/server/routes_workers.py`
- Modify: `src/model_courier/server/migrations/001_initial.sql` only if a persisted presence field is required; prefer existing `workers.last_seen_at`
- Test: `tests/contracts/test_contracts.py`
- Test: `tests/server/test_repository.py`
- Test: `tests/api/test_api.py`

**Interfaces:**
- Consumes: existing `TaskRequirements`, `CapabilityManifest`, `TaskRepository.claim_next`, and Worker registration/poll routes.
- Produces: optional `TaskRequirements.service_id: str | None`, optional `CapabilityManifest.service_id: str | None`, `TaskRepository.worker_presence(worker_id, now) -> str`, and matching that honors service ID before provider/model constraints.

- [ ] **Step 1: Write the failing contract tests**

Add tests that construct a task requiring `service_id="desktop-gpu-1"`, verify it round-trips, and verify a capability with another service ID is rejected. Add a repository test with two otherwise identical capabilities where only the requested service ID wins, plus a legacy task without service ID that still matches. Add a presence test showing `online` at `last_seen_at + 30` and `offline` at `last_seen_at + 90`.

- [ ] **Step 2: Run the focused tests to verify RED**

Run `python -m pytest tests/contracts/test_contracts.py tests/server/test_repository.py -q`. Expected: failures because the fields and presence method do not exist.

- [ ] **Step 3: Implement the minimal protocol and repository changes**

Add bounded optional `service_id` fields to both contracts. In `_matches`, reject a capability when the task requests a different service ID, while leaving provider/model/format matching unchanged. Add `worker_presence` using the existing `last_seen_at` column and constants of 30 seconds for online and 90 seconds for offline. Keep registration and polling payloads backward-compatible.

- [ ] **Step 4: Add API-level coverage and run the focused suite**

Extend API tests to register a service ID and assert it appears in the poll capability selection. Run `python -m pytest tests/contracts/test_contracts.py tests/server/test_repository.py tests/api/test_api.py -q`. Expected: all focused tests pass.

- [ ] **Step 5: Run lint and commit**

Run `ruff check src tests`. Commit with `git add src tests && git commit -m "feat: add stable service routing and presence"`.

### Task 2: Local Agent profiles and Python JSONL bridge

**Files:**
- Create: `src/model_courier/agent/__init__.py`
- Create: `src/model_courier/agent/models.py`
- Create: `src/model_courier/agent/environments.py`
- Create: `src/model_courier/agent/python_bridge.py`
- Test: `tests/agent/test_environments.py`
- Test: `tests/agent/test_python_bridge.py`

**Interfaces:**
- Consumes: Python standard library `subprocess`, `json`, `pathlib`, and the versioned `CapabilityManifest`/`ProviderResult` contracts.
- Produces: `EnvironmentProfile`, `ExecutionProfile`, `ModelBinding`, `EnvironmentInspector.inspect(profile) -> EnvironmentCheck`, and `PythonBridge.run(request) -> dict[str, Any]`.

- [ ] **Step 1: Write failing environment and bridge tests**

Test that `EnvironmentProfile(kind="python", executable=Path(...))` rejects a missing/non-file executable, while a real `sys.executable` returns `available=True` and its Python version. Test `PythonBridge` against a temporary Python script that reads one JSON object per line and emits one response with the same request ID. Add tests for invalid JSON, a response larger than 1 MiB, and a non-zero child exit returning a redacted `BridgeError`.

- [ ] **Step 2: Run `python -m pytest tests/agent -q` and verify RED**

Expected: import failures because the new `agent` package and classes do not exist.

- [ ] **Step 3: Implement environment models and inspector**

Use strict Pydantic models with profile IDs, display names, executable path, optional Conda prefix, and an allowlisted environment-variable map. `EnvironmentInspector` must invoke `[executable, "-c", "import sys; print(sys.version_info[:3])"]` without `shell=True`, with a 10-second timeout and output truncation. Return structured availability, version, and error code; never include environment variable values in the error.

- [ ] **Step 4: Implement the JSONL bridge**

Start the selected executable with an argument vector and an adapter module/script supplied by trusted local configuration. Send one bounded JSON request per line, require matching `request_id`, reject malformed or oversized output, enforce a per-call timeout, terminate the process on timeout, and redact values for keys matching `token`, `secret`, `password`, or `authorization` in diagnostics. Do not accept a remote task as the command or module path.

- [ ] **Step 5: Run focused and full Python tests, then commit**

Run `python -m pytest tests/agent -q`, then `python -m pytest -q`, then `ruff check src tests`. Commit with `git add src tests && git commit -m "feat: add local agent environments and python bridge"`.

### Task 3: Configured HTTP model adapter

**Files:**
- Create: `src/model_courier/agent/http_adapter.py`
- Modify: `src/model_courier/agent/models.py`
- Test: `tests/agent/test_http_adapter.py`

**Interfaces:**
- Consumes: `ExecutionProfile`, `TaskEnvelope`, `ProviderResult`, and `httpx.Client`.
- Produces: `HttpAdapterConfig`, `HttpModelAdapter.check() -> CheckResult`, and `HttpModelAdapter.execute(task, input_bytes) -> ProviderResult`.

- [ ] **Step 1: Write failing HTTP adapter tests**

Use `httpx.MockTransport` to test a multipart synchronous transcription request, configured request field mapping, JSON response extraction from `result.text`, missing extraction paths, non-2xx responses, oversized responses, and request timeout. Assert returned results contain only the normalized schema, never raw response objects.

- [ ] **Step 2: Run `python -m pytest tests/agent/test_http_adapter.py -q` and verify RED**

Expected: import failures for the adapter classes.

- [ ] **Step 3: Implement bounded configuration and adapter**

Support method, relative path, timeout, input field name, optional model field, and a dot-separated response path. Restrict URL construction to a trusted profile base URL; do not read URL, headers, or executable settings from the task. Enforce 32 MiB response size and return `ProviderError`-compatible structured errors for timeout, connection, protocol, and model failures.

- [ ] **Step 4: Run the full suite and commit**

Run `python -m pytest -q` and `ruff check src tests`. Commit with `git add src tests && git commit -m "feat: add configurable http model adapter"`.

### Task 4: Loopback Agent API and Flutter desktop shell

**Files:**
- Create: `src/model_courier/agent/api.py`
- Create: `src/model_courier/agent/store.py`
- Create: `desktop/pubspec.yaml`
- Create: `desktop/lib/main.dart`
- Create: `desktop/lib/agent_api.dart`
- Create: `desktop/lib/screens/overview_page.dart`
- Create: `desktop/lib/screens/model_wizard_page.dart`
- Create: `desktop/test/agent_api_test.dart`
- Modify: `README.md`
- Test: `tests/agent/test_api.py`

**Interfaces:**
- Consumes: Agent models, environment inspector, HTTP adapter, and existing platform client behavior.
- Produces: loopback endpoints for `/v1/session`, `/v1/environments`, `/v1/models`, `/v1/models/{id}/test`, `/v1/accepting`, and `/v1/events`; Flutter screens for overview and model wizard.

- [ ] **Step 1: Write failing Agent API tests**

Test that a session token is required for every endpoint, profiles persist without secret values, model test results expose a stage and redacted error, and accepting work is refused when no model binding is verified. Test that a second start request is idempotent and returns the same operation state.

- [ ] **Step 2: Run `python -m pytest tests/agent/test_api.py -q` and verify RED**

Expected: import failures because the Agent API and store do not exist.

- [ ] **Step 3: Implement the loopback API and SQLite profile store**

Bind to `127.0.0.1` on an ephemeral port, create a per-session token, persist profile metadata in a user data SQLite database, store platform/HTTP secrets through an injected secret-store interface, and implement bounded event polling. Reuse the existing Worker runtime only through a service boundary; do not import model frameworks into API routes.

- [ ] **Step 4: Scaffold the Flutter shell**

Create a desktop Flutter app that calls the loopback API through `AgentApi`, displays platform/Agent/accepting states, lists model bindings, and provides the first three wizard choices: existing HTTP, Python environment, and managed HTTP. Keep all model-specific fields driven by server-provided descriptors; do not add provider logic to Dart.

- [ ] **Step 5: Run validation and document local startup**

Run `python -m pytest -q`, `ruff check src tests`, `python -m compileall -q src packages`, and `flutter analyze desktop` when Flutter is available. Update README with the Agent/Flutter development startup commands and explicitly label the Flutter shell as Windows-first preview if packaging is not yet automated.

- [ ] **Step 6: Commit the vertical slice**

Commit with `git add src tests desktop README.md && git commit -m "feat: add loopback agent api and flutter shell"`.
