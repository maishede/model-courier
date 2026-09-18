"""FastAPI dependencies and error mapping."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Header, HTTPException, Request

from .auth import AuthenticationError, Principal
from .repository import ConflictError, LeaseConflictError, NotFoundError


def get_state(request: Request) -> Any:
    return request.app.state.model_courier


def bearer_value(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="missing bearer token")
    return token


def device_principal(
    authorization: str | None = Header(default=None), state: Any = Depends(get_state)
) -> Principal:
    try:
        return state.tokens.authenticate("device", bearer_value(authorization))
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def worker_principal(
    authorization: str | None = Header(default=None), state: Any = Depends(get_state)
) -> Principal:
    try:
        return state.tokens.authenticate("worker", bearer_value(authorization))
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def map_repository_error(exc: Exception) -> HTTPException:
    if isinstance(exc, NotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, LeaseConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=500, detail="internal repository error")
