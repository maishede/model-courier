"""Loopback-only API consumed by the Flutter desktop shell."""

from __future__ import annotations

import secrets
import tempfile
import time
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from model_courier.contracts import ArtifactRef, TaskEnvelope
from model_courier.worker.provider import ExecutionContext

from .environments import EnvironmentInspector
from .http_adapter import HttpAdapterConfig, HttpModelAdapter
from .models import EnvironmentProfile, ModelBinding
from .providers import BindingProvider
from .runtime import AgentRuntimeController, RuntimeUnavailable
from .store import AgentStore
from .worker import AgentWorkerLoop


class AcceptingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class AgentState:
    def __init__(
        self,
        store: AgentStore,
        session_token: str,
        runtime: AgentRuntimeController,
        http_client: httpx.Client | None,
    ) -> None:
        self.store = store
        self.session_token = session_token
        self.inspector = EnvironmentInspector()
        self.runtime = runtime
        self.http_client = http_client


def create_agent_app(
    path: Path,
    *,
    session_token: str | None = None,
    runtime: AgentRuntimeController | None = None,
    http_client: httpx.Client | None = None,
    platform_url: str | None = None,
    worker_token: str | None = None,
) -> FastAPI:
    store = AgentStore(path)
    store.set_accepting(False)
    if runtime is None and platform_url and worker_token:
        runtime = AgentRuntimeController(AgentWorkerLoop(store, platform_url, worker_token))
    state = AgentState(
        store,
        session_token or secrets.token_urlsafe(32),
        runtime or AgentRuntimeController(None),
        http_client,
    )
    app = FastAPI(title="ModelCourier Local Agent", version="0.1.0")
    app.state.model_courier_agent = state

    def authorize(authorization: str | None = Header(default=None)) -> AgentState:
        if authorization != f"Bearer {state.session_token}":
            raise HTTPException(status_code=401, detail="invalid local session")
        return state

    @app.get("/v1/session")
    def session(agent: AgentState = Depends(authorize)) -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/environments")
    def environments(agent: AgentState = Depends(authorize)) -> list[dict]:
        return []

    @app.get("/v1/status")
    def status(agent: AgentState = Depends(authorize)) -> dict[str, str | bool]:
        runtime_status = agent.runtime.status()
        accepting = agent.store.accepting()
        if accepting and runtime_status in {"error", "stopped"}:
            agent.store.set_accepting(False)
            accepting = False
        return {"accepting": accepting, "runtime": runtime_status}

    @app.get("/v1/models")
    def models(agent: AgentState = Depends(authorize)) -> list[dict]:
        return [
            {
                **binding.model_dump(mode="json"),
                "verified": agent.store.is_verified(binding.binding_id),
            }
            for binding in agent.store.list_bindings()
        ]

    @app.post("/v1/models", status_code=201)
    def save_model(binding: ModelBinding, agent: AgentState = Depends(authorize)) -> dict:
        if agent.store.accepting():
            agent.runtime.stop()
            agent.store.set_accepting(False)
        try:
            agent.store.save_binding(binding)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return binding.model_dump(mode="json")

    @app.post("/v1/models/{binding_id}/test")
    def test_model(binding_id: str, agent: AgentState = Depends(authorize)) -> dict:
        binding = agent.store.get_binding(binding_id)
        if binding is None:
            raise HTTPException(status_code=404, detail="model binding not found")
        if binding.execution.kind == "python":
            executable = binding.execution.config.get("executable")
            if not isinstance(executable, str):
                return _failed_test(
                    "environment", "executable_missing", "Python executable is not configured"
                )
            check = agent.inspector.inspect(
                EnvironmentProfile(
                    profile_id=binding.execution.profile_id,
                    display_name=binding.display_name,
                    executable=executable,
                )
            )
            if not check.available:
                return _failed_test(
                    "environment",
                    check.error_code or "probe_failed",
                    check.message or "environment unavailable",
                )
            return {
                "status": "succeeded",
                "stage": "environment",
                "python_version": check.python_version,
            }
        if binding.execution.kind == "http":
            try:
                adapter = HttpModelAdapter(
                    HttpAdapterConfig.model_validate(binding.execution.config),
                    client=agent.http_client,
                )
            except ValueError:
                return _failed_test(
                    "configuration", "invalid_http_config", "HTTP configuration is invalid"
                )
            try:
                check = adapter.check()
            finally:
                adapter.close()
            if not check.available:
                return _failed_test(
                    "connection",
                    check.error_code or "connection_error",
                    "HTTP service is unavailable",
                )
            return {
                "status": "succeeded",
                "stage": "connection",
                "status_code": check.status_code,
            }
        return _failed_test(
            "startup", "managed_http_pending", "managed HTTP startup is not available yet"
        )

    @app.post("/v1/models/{binding_id}/verify")
    async def verify_model(
        binding_id: str, request: Request, agent: AgentState = Depends(authorize)
    ) -> dict:
        binding = agent.store.get_binding(binding_id)
        if binding is None:
            raise HTTPException(status_code=404, detail="model binding not found")
        content = await request.body()
        if not content:
            return _failed_test("inference", "sample_missing", "verification sample is empty")
        if len(content) > 32 * 1024 * 1024:
            return _failed_test(
                "inference", "sample_too_large", "verification sample exceeded the size limit"
            )
        task = _verification_task(binding, request, len(content))
        if binding.execution.kind == "python":
            verification_path = Path(tempfile.gettempdir()) / (
                f"model-courier-verify-{secrets.token_hex(8)}"
            )
            verification_path.write_bytes(content)
            provider = BindingProvider(binding)
            try:
                await run_in_threadpool(provider.validate, task)
                result = await run_in_threadpool(
                    provider.execute,
                    task,
                    ExecutionContext(
                        task_id=task.idempotency_key,
                        deadline=time.time() + 60.0,
                        input_paths=[verification_path],
                    ),
                )
            except Exception as exc:
                code = getattr(exc, "code", "verification_failed")
                return _failed_test("inference", code, str(exc)[:512])
            finally:
                provider.close()
                verification_path.unlink(missing_ok=True)
            agent.store.mark_verified(binding_id)
            return {
                "status": "succeeded",
                "stage": "inference",
                "result": result.model_dump(mode="json", by_alias=True),
            }
        if binding.execution.kind != "http":
            return _failed_test(
                "startup", "managed_http_pending", "managed HTTP verification is not available yet"
            )
        try:
            adapter = HttpModelAdapter(
                HttpAdapterConfig.model_validate(binding.execution.config),
                client=agent.http_client,
            )
            try:
                result = adapter.execute(task, content)
            finally:
                adapter.close()
        except ValueError:
            return _failed_test(
                "configuration", "invalid_http_config", "HTTP configuration is invalid"
            )
        except Exception as exc:
            code = getattr(exc, "code", "verification_failed")
            return _failed_test("inference", code, str(exc)[:512])
        agent.store.mark_verified(binding_id)
        return {
            "status": "succeeded",
            "stage": "inference",
            "result": result.model_dump(mode="json", by_alias=True),
        }

    @app.post("/v1/accepting")
    def accepting(
        request: AcceptingRequest, agent: AgentState = Depends(authorize)
    ) -> dict[str, bool]:
        if request.enabled:
            if not agent.store.has_verified_enabled_binding():
                raise HTTPException(status_code=409, detail="no verified enabled model binding")
            try:
                agent.runtime.start()
            except RuntimeUnavailable as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
        else:
            agent.runtime.stop()
        agent.store.set_accepting(request.enabled)
        return {"accepting": agent.store.accepting()}

    @app.get("/v1/events")
    def events(agent: AgentState = Depends(authorize)) -> dict[str, list]:
        return {"events": []}

    return app


def _failed_test(stage: str, code: str, message: str) -> dict:
    return {"status": "failed", "stage": stage, "error": {"code": code, "message": message}}


def _verification_task(binding: ModelBinding, request: Request, size_bytes: int) -> TaskEnvelope:
    return TaskEnvelope(
        task_type=binding.task_type,
        input_artifacts=[
            ArtifactRef(
                name="verification-input",
                mime=request.headers.get("content-type", "application/octet-stream").split(
                    ";", 1
                )[0],
                size_bytes=size_bytes,
            )
        ],
        idempotency_key=f"local-verify:{binding.binding_id}:{secrets.token_hex(8)}",
        requires={"service_id": binding.service_id},
    )
