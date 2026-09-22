"""Pydantic request/response schemas for API contract v0.3.1.

**欄位名不准自創。** 契約要改 → 開 SPEC-QUESTION，不要在這裡偷加欄位。
"""

from datetime import datetime
from typing import Any

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from apps.api.enums import AgentSourceType, AgentVersionStatus, ErrorCode, EventType


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunConfig(StrictRequest):
    timeout_seconds: int = Field(default=900, ge=1, le=900, strict=True)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateRunRequest(StrictRequest):
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


class RunSummary(BaseModel):
    run_id: str
    agent_id: str
    agent_version: str
    status: str
    trigger: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class RunListResponse(BaseModel):
    items: list[RunSummary]
    next_cursor: str | None


class CreateAgentRequest(StrictRequest):
    name: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    description: str | None = Field(default=None, max_length=4000)


class UpdateAgentRequest(StrictRequest):
    name: str | None = Field(default=None, min_length=1, max_length=255,
                             pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    description: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def not_empty(self):
        if not self.model_fields_set:
            raise ValueError("at least one metadata field is required")
        return self


class AgentSummary(BaseModel):
    agent_id: str
    name: str
    description: str | None
    latest_published_version: str | None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CreateAgentResponse(BaseModel):
    agent_id: str
    name: str
    description: str | None
    latest_published_version: str | None
    created_at: datetime


class AgentEndpoints(BaseModel):
    rest: str
    mcp: str
    openapi: str


class AgentDetail(AgentSummary):
    endpoints: AgentEndpoints


class AgentListResponse(BaseModel):
    items: list[AgentSummary]
    next_cursor: str | None


def parse_image_ref(value: str) -> tuple[str, str]:
    if value.count("@") != 1:
        raise ValueError("image_ref must contain exactly one digest separator")
    repository, digest = value.split("@", 1)
    if not repository or "/" not in repository or repository != repository.lower():
        raise ValueError("image_ref must contain a canonical lowercase repository")
    if any(char.isspace() for char in repository) or ":" in repository.rsplit("/", 1)[-1]:
        raise ValueError("image_ref must not contain a tag")
    prefix, separator, hex_digest = digest.partition(":")
    if separator != ":" or prefix != "sha256" or len(hex_digest) != 64:
        raise ValueError("image_ref must use a complete sha256 digest")
    if any(char not in "0123456789abcdef" for char in hex_digest):
        raise ValueError("image_ref digest must be lowercase hexadecimal")
    return repository, digest


class CreateAgentVersionRequest(StrictRequest):
    semver: str = Field(min_length=1, max_length=64,
                        pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
    image_ref: str = Field(min_length=1, max_length=2048)

    @field_validator("image_ref")
    @classmethod
    def canonical_image_ref(cls, value: str) -> str:
        parse_image_ref(value)
        return value


class ImageArtifact(BaseModel):
    image_ref: str
    digest: str
    platform: str | None
    size_bytes: int | None
    runtime_contract: str | None


class AgentVersionResponse(BaseModel):
    agent_version_id: str
    semver: str
    version_id: str
    source_type: AgentSourceType
    artifact: ImageArtifact
    status: AgentVersionStatus
    error: dict[str, Any] | None
    created_at: datetime
    manifest: dict[str, Any] | None = None
    entrypoints: list[dict[str, Any]] | None = None


class AgentVersionListResponse(BaseModel):
    items: list[AgentVersionResponse]
    next_cursor: str | None


# ---- Internal Gateway（沙盒專用，不對外）----

class BootstrapResponse(BaseModel):
    run_id: str
    entrypoint: str
    input: dict[str, Any]
    manifest: dict[str, Any]


class ValidationPayload(StrictRequest):
    valid: bool
    runtime_contract: str
    manifest: dict[str, Any] | None
    entrypoints: list[dict[str, Any]] | None
    error: dict[str, Any] | None

    @model_validator(mode="after")
    def complete_validation(self):
        if self.valid:
            if self.runtime_contract != "zentra.image/v1":
                raise ValueError("runtime_contract must be zentra.image/v1")
            if self.manifest is None or not self.entrypoints or self.error is not None:
                raise ValueError("valid payload requires manifest and entrypoints only")
        else:
            if self.manifest is not None or self.entrypoints is not None or self.error is None:
                raise ValueError("invalid payload requires only an error")
            if self.error.get("code") != ErrorCode.BUILD_FAILED:
                raise ValueError("validation error code must be BUILD_FAILED")
        names: set[str] = set()
        for entrypoint in self.entrypoints or []:
            required = {"name", "description", "input_schema", "output_schema"}
            if set(entrypoint) != required or not isinstance(entrypoint["name"], str):
                raise ValueError("entrypoint shape is invalid")
            if entrypoint["name"] in names:
                raise ValueError("entrypoint names must be unique")
            names.add(entrypoint["name"])
            for key in ("input_schema", "output_schema"):
                schema = entrypoint[key]
                if not isinstance(schema, dict) or schema.get("type") != "object":
                    raise ValueError(f"{key} must be an object JSON Schema")
        return self


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
