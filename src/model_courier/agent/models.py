"""Configuration models shared by the local Agent and its GUI."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EnvironmentProfile(AgentModel):
    profile_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)
    executable: Path
    conda_prefix: Path | None = None
    environment: dict[str, str] = Field(default_factory=dict)


class ExecutionProfile(AgentModel):
    profile_id: str = Field(min_length=1, max_length=128)
    kind: Literal["python", "http", "managed_http"]
    environment_id: str | None = Field(default=None, max_length=128)
    adapter_id: str = Field(min_length=1, max_length=128)
    config: dict[str, Any] = Field(default_factory=dict)


class ModelBinding(AgentModel):
    binding_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)
    service_id: str = Field(min_length=1, max_length=128)
    execution: ExecutionProfile
    model_name: str = Field(min_length=1, max_length=256)
    enabled: bool = False
