"""Device-facing REST endpoints."""

from __future__ import annotations

import json
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from model_courier.contracts import ErrorEnvelope, TaskEnvelope, canonical_json_digest

from .dependencies import device_principal, get_state, map_repository_error
from .quotas import QuotaExceededError
from .repository import TaskRecord
from .storage import ArtifactError

router = APIRouter(tags=["devices"])


def _view(task: TaskRecord, upload_url: str | None = None) -> dict:
    error = ErrorEnvelope.model_validate_json(task.error_json) if task.error_json else None
    return {
        "task_id": task.id,
        "status": task.status,
        "task_type": task.task_type,
        "expires_at": task.expires_at,
        "attempt": task.attempt,
        "result_available": task.result_available,
        "result_expires_at": task.result_expires_at,
        "upload_url": upload_url,
        "error": error,
    }


@router.post("/tasks", response_model=dict, status_code=201)
def create_task(
    envelope: TaskEnvelope,
    principal=Depends(device_principal),
    state=Depends(get_state),
) -> dict:
    request_digest = canonical_json_digest(envelope)
    declared_size = sum(item.size_bytes or 0 for item in envelope.input_artifacts)
    try:
        state.quota.reserve(principal.owner_id, principal.subject_id, declared_size)
        task = state.repository.create_uploading(
            principal.owner_id,
            principal.subject_id,
            envelope,
            request_digest,
        )
    except QuotaExceededError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        raise map_repository_error(exc) from exc
    return _view(task, f"/v1/tasks/{task.id}/input")


@router.put("/tasks/{task_id}/input", response_model=dict)
async def upload_input(
    task_id: str,
    request: Request,
    name: Annotated[str, Query(min_length=1, max_length=255)],
    mime: Annotated[str, Query(min_length=1, max_length=128)],
    size_bytes: Annotated[int, Query(ge=0)],
    principal=Depends(device_principal),
    state=Depends(get_state),
) -> dict:
    try:
        task = state.repository.get_task(principal.owner_id, task_id)
        if task.status != "uploading":
            raise HTTPException(status_code=409, detail="task is no longer accepting input")
        handle = state.storage.begin(task_id, "input", size_bytes, mime, name)
        artifact = await state.storage.write_async_stream(handle, request.stream())
        request_digest = canonical_json_digest(json.loads(task.request_json))
        task = state.repository.finalize_and_enqueue(
            task_id,
            artifact,
            request_digest,
            artifact_path=str(handle.path.relative_to(state.storage.root)),
        )
    except HTTPException:
        raise
    except ArtifactError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except Exception as exc:
        raise map_repository_error(exc) from exc
    return _view(task)


@router.post("/tasks/{task_id}/submit", response_model=dict)
def submit_task(
    task_id: str,
    principal=Depends(device_principal),
    state=Depends(get_state),
) -> dict:
    try:
        task = state.repository.get_task(principal.owner_id, task_id)
    except Exception as exc:
        raise map_repository_error(exc) from exc
    if task.status == "uploading":
        raise HTTPException(status_code=409, detail="input has not been uploaded")
    return _view(task)


@router.get("/tasks/{task_id}", response_model=dict)
def get_task(
    task_id: str,
    principal=Depends(device_principal),
    state=Depends(get_state),
) -> dict:
    try:
        return _view(state.repository.get_task(principal.owner_id, task_id))
    except Exception as exc:
        raise map_repository_error(exc) from exc


@router.get("/tasks/{task_id}/result")
def get_result(
    task_id: str,
    principal=Depends(device_principal),
    state=Depends(get_state),
) -> Response:
    try:
        task = state.repository.get_task(principal.owner_id, task_id)
    except Exception as exc:
        raise map_repository_error(exc) from exc
    if task.status != "succeeded" or not task.result_available:
        if task.status == "succeeded":
            raise HTTPException(status_code=410, detail="result has expired")
        raise HTTPException(status_code=409, detail="result is not ready")
    if task.result_expires_at is not None and task.result_expires_at <= time.time():
        raise HTTPException(status_code=410, detail="result has expired")
    return Response(content=task.result_json or "{}", media_type="application/json")


@router.post("/tasks/{task_id}/cancel", response_model=dict)
def cancel_task(
    task_id: str,
    principal=Depends(device_principal),
    state=Depends(get_state),
) -> dict:
    try:
        task = state.repository.cancel(principal.owner_id, task_id, now=time.time())
    except Exception as exc:
        raise map_repository_error(exc) from exc
    return _view(task)
