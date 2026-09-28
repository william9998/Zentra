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


class SetAgentLlmCredentialRequest(BaseModel):
    """註冊或覆寫一個 Agent 的 LLM 憑證（ZEN-012）。

    **覆寫會立刻對該 Agent 的所有版本生效** —— 憑證綁 Agent 不綁版本（ADR-0011）。
    """

    api_key: str = Field(min_length=8, max_length=512,
                         description="供應商金鑰。寫進去就讀不出來了。")
    base_url: str | None = Field(
        default=None, max_length=2048,
        description="OpenAI 相容端點。省略則用平台預設。金鑰與端點通常是一組的。",
    )
    default_model: str | None = Field(
        default=None, max_length=128,
        description="這個 Agent 沒指定 model 時用哪個。省略則用平台預設。",
    )


class AgentLlmCredentialResponse(BaseModel):
    """**永遠不含金鑰**，連密文都沒有。`last_four` 是唯一的線索。"""

    configured: bool
    last_four: str | None = None
    base_url: str | None = None
    default_model: str | None = None
    updated_at: datetime | None = None


class IssueAgentApiKeyRequest(StrictRequest):
    """發一把新的下游 key（ZEN-015）。"""

    name: str = Field(
        min_length=1, max_length=128,
        description="給人看的名字，例如下游應用名。撤銷時靠它認出是哪一把。",
    )


class AgentApiKeyResponse(BaseModel):
    """**不含 key 本身**。`key_prefix` 只是辨識用的前幾碼。"""

    key_id: str
    agent_id: str
    name: str
    key_prefix: str
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class IssuedAgentApiKeyResponse(AgentApiKeyResponse):
    """建立時的回應，**唯一一次**看得到明文。

    平台只存雜湊，所以這個值遺失了就只能重發一把。這是刻意的。
    """

    key: str


class AgentApiKeyListResponse(BaseModel):
    """含已撤銷的 key —— 歷史用量還掛在它們身上。"""

    items: list[AgentApiKeyResponse]


class UsageTotals(BaseModel):
    """與契約 §1 的 Run `usage` 同一個形狀，所以前端只要會讀一種。"""

    llm_calls: int
    input_tokens: int
    output_tokens: int
    #: 十進位字串，不是 float。這是帳，浮點誤差會變成對不起來的數字。
    cost_usd: str


class UsageByKey(UsageTotals):
    """`key_id` 為 null 代表 Console 發起、沒有歸戶的用量 —— 那一列要留著。"""

    key_id: str | None = None
    name: str | None = None
    key_prefix: str | None = None


class UsageByModel(UsageTotals):
    model: str


class AgentUsageResponse(BaseModel):
    """三種切面一次回。除錯時它們是一起看的，拆成三次往返只是把拼裝推給前端。"""

    totals: UsageTotals
    by_key: list[UsageByKey]
    by_model: list[UsageByModel]


class LlmCallSummary(BaseModel):
    """一次 LLM 呼叫。帶著 `run_id` 與 `api_key_id`，所以查到哪筆錯就能直接跳過去。"""

    call_id: str
    run_id: str
    run_status: str
    api_key_id: str | None = None
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: str
    latency_ms: int | None = None
    status_code: int | None = None
    error: str | None = None
    credential_source: str
    created_at: datetime


class LlmCallListResponse(BaseModel):
    items: list[LlmCallSummary]
    #: 無下一頁為 null。**key 一定在**（契約 §0），不然前端只能靠它在不在去猜。
    next_cursor: str | None = None


class AgentContainerResponse(BaseModel):
    """常駐容器的狀態（ZEN-015）。

    `restart_count` 刻意回給使用者：一直重啟的容器是壞掉了，
    而那件事在畫面上看起來跟「剛啟動」一模一樣，沒有這個數字分不出來。
    """

    container_id: str
    agent_id: str
    agent_version: str
    status: str                 # STARTING | READY | UNHEALTHY | STOPPED | FAILED
    inflight_runs: int
    max_concurrent_runs: int
    restart_count: int
    started_at: datetime | None = None
    last_health_at: datetime | None = None
    error: str | None = None


class AgentContainerListResponse(BaseModel):
    items: list[AgentContainerResponse]


class StartAgentContainerRequest(StrictRequest):
    """省略 `version` 就用最新的 `PUBLISHED`。"""

    version: str | None = None
