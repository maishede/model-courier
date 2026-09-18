"""HTTP request and response schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from model_courier.contracts import CapabilityManifest, ErrorEnvelope


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskView(StrictModel):
    task_id: str
    status: str
    task_type: str
    expires_at: float
    attempt: int
    result_available: bool
    result_expires_at: float | None = None
    upload_url: str | None = None
    error: ErrorEnvelope | None = None


class WorkerRegistration(StrictModel):
    capabilities: list[CapabilityManifest] = Field(min_length=1, max_length=64)


class WorkerPollRequest(StrictModel):
    wait_seconds: int = Field(default=25, ge=0, le=25)


class LeaseRequest(StrictModel):
    lease_generation: int = Field(ge=1)
    lease_token: str = Field(min_length=16, max_length=256)


class WorkerFailure(LeaseRequest):
    error: ErrorEnvelope
