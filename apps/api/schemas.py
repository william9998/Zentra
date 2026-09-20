"""Pydantic 請求/回應 —— 逐欄位對應 specs/api-contract.md v0.2.2。

**欄位名不准自創。** 契約要改 → 開 SPEC-QUESTION，不要在這裡偷加欄位。
"""

from datetime import datetime
from typing import Any

from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from apps.api.enums import ErrorCode, EventType


class RunConfig(BaseModel):
    timeout_seconds: int = Field(default=900, ge=1, le=900, strict=True)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateRunRequest(BaseModel):
    entrypoint: str = "default"
    input: dict[str, Any]
    version: str | None = None
    config: RunConfig = Field(default_factory=RunConfig)


class CreateRunResponse(BaseModel):
    run_id: str
    agent_id: str
    agent_version: str
    status: str
    created_at: datetime
    events_url: str


class RunResponse(BaseModel):
    run_id: str
    agent_id: str
    agent_version: str
    status: str
    trigger: str
    input: dict[str, Any]
    output: dict[str, Any] | None
    error: dict[str, Any] | None
    usage: dict[str, Any] | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


# ---- Internal Gateway（沙盒專用，不對外）----

class BootstrapResponse(BaseModel):
    run_id: str
    entrypoint: str
    input: dict[str, Any]
    manifest: dict[str, Any]


class SandboxEvent(BaseModel):
    event_id: UUID
    type: EventType
    payload: dict[str, Any]

    @field_validator("event_id")
    @classmethod
    def uuid_version(cls, value: UUID) -> UUID:
        if value.version != 7:
            raise ValueError("event_id must be UUID v7")
        return value

    @field_validator("type")
    @classmethod
    def sandbox_event_type(cls, value: EventType) -> EventType:
        if value == EventType.RUN_STATUS:
            raise ValueError("run.status is reserved for the worker")
        return value


class SandboxResult(BaseModel):
    output: dict[str, Any] | None
    error: dict[str, Any] | None
    usage: dict[str, Any] | None

    @model_validator(mode="after")
    def complete_result(self):
        if (self.output is None) == (self.error is None):
            raise ValueError("provide either output or error")
        if self.error is not None:
            ErrorCode(self.error.get("code"))
        return self
