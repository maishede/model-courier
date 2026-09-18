"""Stable API and Worker error contract."""

from pydantic import BaseModel, ConfigDict, Field


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=512)
    retryable: bool = False
    request_id: str = Field(min_length=1, max_length=128)
