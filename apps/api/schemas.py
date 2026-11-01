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
    #: Agent 名稱。**跟 id 一起給** —— 只有 id 的話列表要人肉比對 UUID，
    #: 只有名稱的話又跳不到那個 Agent。兩個都要。
    agent_name: str
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
    #: 這個 Agent 沒指定 model 時要用哪個（憑證設定 → 平台預設）。
    #: **平台算好再送進沙盒** —— SDK 自己猜一個的話，使用者在 Console 上設的值
    #: 永遠到不了，而且「沒生效」看起來跟「生效了」一模一樣。
    default_model: str = ""
    #: ZEN-019：§2.6 的設定，**原封不動**。
    #: **頂層欄位，不得合併進 `manifest`**（契約 §4.1 第 1 點）——
    #: manifest 是版本的不可變內容，把可變設定攪進去會讓 P5 在 runtime
    #: 這一側失去實體。SDK 的 `ctx.config` 讀這個欄位。
    config: dict[str, Any] = Field(default_factory=dict)
    #: ZEN-019：排序後的機密名字。**沒有值、沒有長度、沒有尾四碼。**
    #: 給名字是為了讓 agent 能 fail fast 說出「缺 SMTP_PASSWORD」。
    secret_names: list[str] = Field(default_factory=list)


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


class CreateMcpKeyRequest(StrictRequest):
    """發一把 MCP key，並勾選它看得到的 Agent。"""

    name: str = Field(min_length=1, max_length=128,
                      description="給人看的名字，例如對端 AI 平台名。")
    agent_ids: list[str] = Field(default_factory=list, max_length=200)


class SetMcpKeyAgentsRequest(StrictRequest):
    """整份取代這把 key 的勾選。"""

    agent_ids: list[str] = Field(max_length=200)


class McpKeyResponse(BaseModel):
    """**不含 key 本身**。`agent_ids` 只含仍存在的 Agent。"""

    key_id: str
    name: str
    key_prefix: str
    agent_ids: list[str]
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class IssuedMcpKeyResponse(McpKeyResponse):
    """建立時的回應，**唯一一次**看得到明文。"""

    key: str


class McpKeyListResponse(BaseModel):
    """含已撤銷的 key —— 歷史 Run 還掛在它們身上。"""

    endpoint: str
    items: list[McpKeyResponse]


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


# ---- ZEN-019：per-agent 設定、機密、對外能力（api-contract §2.6–§2.10）----


class AgentConfigRequest(StrictRequest):
    """`PUT /v1/agents/{id}/config` —— **整份取代**（§2.6）。

    body 必須是 `{"config": {...}}`，**不是裸物件**：留這一層是為了之後要加
    `updated_by` 之類的欄位時不必改形狀。
    """

    config: dict[str, Any] = Field(
        description="原封不動送進沙盒 ctx.config 的 JSON。**這裡不是放密碼的地方**，"
                    "那是 §2.7 的機密。",
    )


class AgentConfigResponse(BaseModel):
    """`config` 這個 key 一定在，不會是 `null`。未設定過時是 `{}` 且 `updated_at: null`。"""

    config: dict[str, Any]
    updated_at: datetime | None


class SetAgentSecretRequest(StrictRequest):
    """`PUT /v1/agents/{id}/secrets/{name}`（§2.7）。名字在路徑上，值在 body。"""

    value: str = Field(min_length=1, max_length=4096,
                       description="寫進去就讀不出來了。沙盒只拿得到名字。")


class AgentSecretResponse(BaseModel):
    """**永遠不含明文，連密文都沒有。** `last_four` 是唯一的線索。"""

    name: str
    last_four: str
    created_at: datetime
    updated_at: datetime


class AgentSecretListResponse(BaseModel):
    items: list[AgentSecretResponse]


class EgressRuleRequest(StrictRequest):
    """出口白名單的一條規則（§2.8）。欄位層級的規則在 `services/egress_rules.py`。

    這裡刻意只做型別與長度，**語意檢查（`*`、scheme、port、IP 字面值、
    method 白名單、auth 成對）全部在服務層**：那些錯誤要說出「哪裡不合法」，
    而 pydantic 的欄位錯誤講不出那種話。
    """

    host: str = Field(min_length=1, max_length=253)
    methods: list[str] | None = Field(default=None, max_length=2)
    path_prefix: str | None = Field(default=None, max_length=1024)
    auth_secret_name: str | None = Field(default=None, max_length=64)
    auth_header: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=200)


class EgressRuleResponse(BaseModel):
    rule_id: str
    agent_id: str
    host: str
    methods: list[str]
    path_prefix: str | None
    auth_secret_name: str | None
    auth_header: str | None
    description: str | None
    created_at: datetime
    updated_at: datetime


class EgressRuleListResponse(BaseModel):
    items: list[EgressRuleResponse]


class EmailSenderRequest(BaseModel):
    """寄件者（§2.9）。**這裡沒有密碼欄位** —— 只能以 `password_secret_name` 指名。"""

    model_config = ConfigDict(extra="forbid")

    smtp_host: str = Field(min_length=1, max_length=253)
    smtp_port: int
    username: str = Field(min_length=1, max_length=320)
    from_address: str = Field(min_length=3, max_length=320)
    password_secret_name: str = Field(min_length=1, max_length=64)
    allowed_recipients: list[str] = Field(min_length=1, max_length=10)

    @model_validator(mode="before")
    @classmethod
    def no_password_here(cls, data):
        """帶密碼進來時要說出**該去哪裡存**，不是只回一句「多了欄位」。

        `extra="forbid"` 會擋下來，但它的訊息是 `extra_forbidden` ——
        看到那個的人會以為欄位名打錯了，然後試 `smtp_password`、`pass`…
        """
        known = {
            "smtp_host", "smtp_port", "username", "from_address",
            "password_secret_name", "allowed_recipients",
        }
        if isinstance(data, dict):
            for key in data:
                lowered = str(key).lower()
                if key in known:
                    continue
                if "password" in lowered or "secret" in lowered:
                    raise ValueError(
                        f"{key!r} does not belong here: this table has no password "
                        "column on purpose. Store the password with "
                        "PUT /v1/agents/{id}/secrets/{name} and point at it with "
                        "password_secret_name -- one place to store secrets means "
                        "one way to encrypt them and one way to leak them."
                    )
        return data


class EmailSenderResponse(BaseModel):
    """未設定時 `configured: false`，其餘欄位 `null`。"""

    configured: bool
    smtp_host: str | None = None
    smtp_port: int | None = None
    username: str | None = None
    from_address: str | None = None
    password_secret_name: str | None = None
    allowed_recipients: list[str] | None = None
    updated_at: datetime | None = None


class EgressCallSummary(BaseModel):
    """一列稽核（§2.10）。HTTP 與寄信共用，用 `kind` 分。"""

    call_id: str
    run_id: str
    kind: str
    rule_id: str | None
    method: str | None
    host: str
    #: **不含 query string。** kind=EMAIL 時是收件人（以 `, ` 串接）。
    path: str
    status_code: int | None
    request_bytes: int
    response_bytes: int
    latency_ms: int | None
    error: str | None
    created_at: datetime


class EgressCallListResponse(BaseModel):
    items: list[EgressCallSummary]
    next_cursor: str | None = None


# ---- Internal Gateway：出口代打與寄信（§4.2、§4.4）----


class EgressRequest(StrictRequest):
    """沙盒送來的代打請求（§4.2）。

    `headers` **只准 Accept 與 Content-Type**，其餘在服務層回 422 並指名 ——
    默默丟掉會讓「我的 key 怎麼沒生效」變成查半天的謎（E3）。
    """

    url: str = Field(min_length=1, max_length=4096)
    method: str = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = None
    #: **`> 15`、`<= 0`、非整數一律 `422`，不夾值**（v0.12.1，U4）。
    #: 靜默夾值會讓「我設了 60 秒卻 15 秒就斷」變成查不出原因的怪事。
    timeout_seconds: int = Field(default=10, ge=1, le=15, strict=True)

    @field_validator("method")
    @classmethod
    def only_get_or_post(cls, value: str) -> str:
        if value not in ("GET", "POST"):
            raise ValueError("only GET and POST are proxied")
        return value

    @model_validator(mode="after")
    def body_needs_post(self):
        if self.body is not None and self.method != "POST":
            raise ValueError("only POST may carry a body")
        return self


class EgressResponseEnvelope(BaseModel):
    """**信封語意**：只要請求真的送出去了，Gateway 回 200，上游狀態碼在 `status`。

    上游回 418／429／500 不是平台錯誤 —— agent 要看得到狀態碼才切得了備援。
    """

    status: int
    headers: dict[str, str]
    body: str


class NotifyEmailRequest(StrictRequest):
    """沙盒送來的寄信請求（§4.4）。

    **不能指定 `from`、SMTP 主機或密碼** —— 那些由平台決定（§2.9）。
    `extra="forbid"` 讓那些欄位直接回 422。
    """

    to: list[str] = Field(min_length=1, max_length=10)
    subject: str = Field(min_length=1, max_length=200)
    text: str | None = None
    html: str | None = None

    @model_validator(mode="after")
    def needs_a_body(self):
        if not self.text and not self.html:
            raise ValueError("provide text, html, or both")
        return self


class NotifyEmailResponse(BaseModel):
    """`202` 表示 SMTP 伺服器**收下了**，不表示對方讀到了。"""

    accepted: bool
    message_id: str
