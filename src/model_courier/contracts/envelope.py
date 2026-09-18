"""Task envelope and artifact contracts shared by devices and Workers."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TaskType = Literal["audio.transcribe.v1", "vision.detect.v1"]
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDEMPOTENCY = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ArtifactRef(ContractModel):
    """A binary or structured artifact referenced by a task or result."""

    artifact_id: str | None = Field(default=None, min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=255)
    mime: str = Field(min_length=1, max_length=128)
    size_bytes: int | None = Field(default=None, ge=0)
    sha256: str | None = None

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str | None) -> str | None:
        if value is not None and not _SHA256.fullmatch(value):
            raise ValueError("sha256 must be a lowercase 64-character hexadecimal digest")
        return value


class TaskEnvelope(ContractModel):
    """The stable semantic task contract sent by a device."""

    protocol_version: Literal["1"] = "1"
    task_type: TaskType
    input_artifacts: list[ArtifactRef] = Field(min_length=1, max_length=8)
    options: dict[str, Any] = Field(default_factory=dict)
    requires: TaskRequirements = Field(default_factory=lambda: TaskRequirements())
    idempotency_key: str = Field(min_length=1, max_length=128)
    ttl_seconds: int = Field(default=86_400, ge=1, le=604_800)

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        if not _IDEMPOTENCY.fullmatch(value):
            raise ValueError("idempotency_key contains unsupported characters")
        return value


def canonical_json_digest(value: BaseModel | dict[str, Any]) -> str:
    """Return a stable SHA-256 digest for a contract or JSON-compatible mapping."""

    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


from .capabilities import TaskRequirements  # noqa: E402  (resolves the forward reference)

TaskEnvelope.model_rebuild()
