"""Loopback-only API consumed by the Flutter desktop shell."""

from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict

from .environments import EnvironmentInspector
from .http_adapter import HttpAdapterConfig, HttpModelAdapter
from .models import EnvironmentProfile, ModelBinding
from .store import AgentStore


class AcceptingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class AgentState:
    def __init__(self, store: AgentStore, session_token: str) -> None:
        self.store = store
        self.session_token = session_token
        self.inspector = EnvironmentInspector()


def create_agent_app(path: Path, *, session_token: str | None = None) -> FastAPI:
    state = AgentState(AgentStore(path), session_token or secrets.token_urlsafe(32))
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

    @app.get("/v1/models")
    def models(agent: AgentState = Depends(authorize)) -> list[dict]:
        return [binding.model_dump(mode="json") for binding in agent.store.list_bindings()]

    @app.post("/v1/models", status_code=201)
    def save_model(binding: ModelBinding, agent: AgentState = Depends(authorize)) -> dict:
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
            agent.store.mark_verified(binding_id)
            return {
                "status": "succeeded",
                "stage": "environment",
                "python_version": check.python_version,
            }
        if binding.execution.kind == "http":
            try:
                adapter = HttpModelAdapter(
                    HttpAdapterConfig.model_validate(binding.execution.config)
                )
            except ValueError:
                return _failed_test(
                    "configuration", "invalid_http_config", "HTTP configuration is invalid"
                )
            check = adapter.check()
            if not check.available:
                return _failed_test(
                    "connection",
                    check.error_code or "connection_error",
                    "HTTP service is unavailable",
                )
            agent.store.mark_verified(binding_id)
            return {
                "status": "succeeded",
                "stage": "connection",
                "status_code": check.status_code,
            }
        return _failed_test(
            "startup", "managed_http_pending", "managed HTTP startup is not available yet"
        )

    @app.post("/v1/accepting")
    def accepting(
        request: AcceptingRequest, agent: AgentState = Depends(authorize)
    ) -> dict[str, bool]:
        if request.enabled and not agent.store.has_verified_enabled_binding():
            raise HTTPException(status_code=409, detail="no verified enabled model binding")
        agent.store.set_accepting(request.enabled)
        return {"accepting": agent.store.accepting()}

    @app.get("/v1/events")
    def events(agent: AgentState = Depends(authorize)) -> dict[str, list]:
        return {"events": []}

    return app


def _failed_test(stage: str, code: str, message: str) -> dict:
    return {"status": "failed", "stage": stage, "error": {"code": code, "message": message}}
