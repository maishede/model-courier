"""Worker-facing REST endpoints."""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse

from model_courier.contracts import ProviderResult

from .dependencies import get_state, map_repository_error, worker_principal
from .repository import Lease
from .schemas import LeaseRequest, WorkerFailure, WorkerPollRequest, WorkerRegistration

router = APIRouter(tags=["workers"])


def _lease_from_request(request: LeaseRequest, principal, task_id: str) -> Lease:
    return Lease(
        task_id=task_id,
        owner_id=principal.owner_id,
        worker_id=principal.subject_id,
        lease_generation=request.lease_generation,
        lease_token=request.lease_token,
        lease_until=0,
        request_json="",
    )


@router.post("/workers/register")
def register_worker(
    registration: WorkerRegistration,
    principal=Depends(worker_principal),
    state=Depends(get_state),
) -> dict:
    try:
        state.repository.update_worker_capabilities(
            principal.subject_id, registration.capabilities, now=time.time()
        )
    except Exception as exc:
        raise map_repository_error(exc) from exc
    return {"worker_id": principal.subject_id, "capabilities": registration.capabilities}


@router.post("/workers/poll", status_code=200)
async def poll_worker(
    request: WorkerPollRequest,
    principal=Depends(worker_principal),
    state=Depends(get_state),
) -> Response:
    deadline = time.monotonic() + request.wait_seconds
    while True:
        try:
            capabilities = state.repository.worker_capabilities(principal.subject_id)
            lease = state.repository.claim_next(principal.subject_id, capabilities, now=time.time())
        except Exception as exc:
            raise map_repository_error(exc) from exc
        if lease is not None:
            return Response(
                content=json.dumps(
                    {
                        "task_id": lease.task_id,
                        "lease_generation": lease.lease_generation,
                        "lease_token": lease.lease_token,
                        "lease_until": lease.lease_until,
                        "task": json.loads(lease.request_json),
                    }
                ),
                media_type="application/json",
            )
        if time.monotonic() >= deadline:
            return Response(status_code=204)
        await asyncio.sleep(0.25)


@router.get("/workers/tasks/{task_id}/input")
def download_input(
    task_id: str,
    principal=Depends(worker_principal),
    state=Depends(get_state),
) -> FileResponse:
    try:
        relative = state.repository.input_artifact_path(principal.owner_id, task_id)
    except Exception as exc:
        raise map_repository_error(exc) from exc
    path = (state.storage.root / relative).resolve()
    if state.storage.root.resolve() not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="input artifact unavailable")
    return FileResponse(path)


@router.post("/workers/tasks/{task_id}/start")
def start_worker_task(
    task_id: str,
    lease_request: LeaseRequest,
    principal=Depends(worker_principal),
    state=Depends(get_state),
) -> dict:
    try:
        task = state.repository.start(_lease_from_request(lease_request, principal, task_id))
    except Exception as exc:
        raise map_repository_error(exc) from exc
    return {"task_id": task.id, "status": task.status, "attempt": task.attempt}


@router.post("/workers/tasks/{task_id}/heartbeat")
def heartbeat_worker_task(
    task_id: str,
    lease_request: LeaseRequest,
    principal=Depends(worker_principal),
    state=Depends(get_state),
) -> dict:
    try:
        task = state.repository.heartbeat(_lease_from_request(lease_request, principal, task_id))
    except Exception as exc:
        raise map_repository_error(exc) from exc
    return {"task_id": task.id, "status": task.status}


@router.put("/workers/tasks/{task_id}/result")
def publish_worker_result(
    task_id: str,
    result: ProviderResult,
    lease_generation: int,
    lease_token: str,
    principal=Depends(worker_principal),
    state=Depends(get_state),
) -> dict:
    lease_request = LeaseRequest(lease_generation=lease_generation, lease_token=lease_token)
    try:
        task = state.repository.publish_result(
            _lease_from_request(lease_request, principal, task_id), result
        )
    except Exception as exc:
        raise map_repository_error(exc) from exc
    return {"task_id": task.id, "status": task.status}


@router.post("/workers/tasks/{task_id}/fail")
def fail_worker_task(
    task_id: str,
    failure: WorkerFailure,
    principal=Depends(worker_principal),
    state=Depends(get_state),
) -> dict:
    try:
        task = state.repository.fail(
            _lease_from_request(failure, principal, task_id),
            failure.error.model_dump(mode="json"),
            failure.error.retryable,
        )
    except Exception as exc:
        raise map_repository_error(exc) from exc
    return {"task_id": task.id, "status": task.status}
