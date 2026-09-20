"""Pydantic 請求/回應 —— 逐欄位對應 specs/api-contract.md v0.2.1。

**欄位名不准自創。** 契約要改 → 開 SPEC-QUESTION，不要在這裡偷加欄位。
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RunConfig(BaseModel):
    timeout_seconds: int | None = Field(default=None, ge=1, le=900)
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
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class SandboxEventBatch(BaseModel):
    events: list[SandboxEvent]


class SandboxResult(BaseModel):
    output: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None
