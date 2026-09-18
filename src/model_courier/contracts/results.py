"""Provider result contracts."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .envelope import ArtifactRef

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ProviderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(min_length=1, max_length=64)
    payload: dict = Field(default_factory=dict, alias="json")
    artifacts: list[ArtifactRef] = Field(default_factory=list, max_length=16)
    result_digest: str = Field(min_length=64, max_length=64)

    @field_validator("result_digest")
    @classmethod
    def validate_result_digest(cls, value: str) -> str:
        if not _SHA256.fullmatch(value):
            raise ValueError("result_digest must be a lowercase 64-character hexadecimal digest")
        return value

    @property
    def json(self) -> dict:
        """Return the wire-format result payload without shadowing BaseModel.json."""

        return self.payload
