"""Worker capability and task-routing contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TaskRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    provider: str | None = Field(default=None, min_length=1, max_length=128)
    model: str | None = Field(default=None, min_length=1, max_length=256)
    service_id: str | None = Field(default=None, min_length=1, max_length=128)
    formats: list[str] = Field(default_factory=list, max_length=16)


class CapabilityManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    capability_id: str = Field(min_length=1, max_length=128)
    task_type: Literal["audio.transcribe.v1", "vision.detect.v1"]
    provider: str = Field(min_length=1, max_length=128)
    service_id: str | None = Field(default=None, min_length=1, max_length=128)
    models: list[str] = Field(default_factory=list, max_length=32)
    formats: list[str] = Field(default_factory=list, max_length=32)
    max_concurrency: int = Field(default=1, ge=1, le=16)
