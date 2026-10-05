"""全平台的枚舉常數 —— 照抄 specs/SPEC.md §9 與 specs/api-contract.md。

**不得自創。** 前端、後端、QA 的狀態顯示與斷言都必須引用這裡，
不要在別處寫字串字面值（spec-keeper 會 grep）。
"""

from enum import StrEnum


class RunStatus(StrEnum):
    """SPEC §9 的唯一定義。

    QUEUED → SCHEDULING → RUNNING → ┬→ SUCCEEDED
                                    ├→ FAILED
                                    └→ CANCELLED

    注意：`WAITING_INPUT`（HITL）已於 SPEC v0.2.0 移出 MVP，**不要加回來**。
    """

    QUEUED = "QUEUED"
    SCHEDULING = "SCHEDULING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL


_TERMINAL = frozenset({RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED})


class RunTrigger(StrEnum):
    API = "API"
    MCP = "MCP"


class AgentVersionStatus(StrEnum):
    BUILDING = "BUILDING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"

    @property
    def is_terminal(self) -> bool:
        return self in {self.PUBLISHED, self.FAILED}


class AgentSourceType(StrEnum):
    OCI_IMAGE = "OCI_IMAGE"


class ErrorCode(StrEnum):
    """api-contract.md §0 的錯誤 code 枚舉。要加先改契約。"""

    UNAUTHENTICATED = "UNAUTHENTICATED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    AGENT_NOT_PUBLISHED = "AGENT_NOT_PUBLISHED"
    #: ZEN-015：呼叫了沒有 READY 常駐容器的 Agent
    AGENT_NOT_RUNNING = "AGENT_NOT_RUNNING"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    BUILD_FAILED = "BUILD_FAILED"
    SANDBOX_TIMEOUT = "SANDBOX_TIMEOUT"
    SANDBOX_ERROR = "SANDBOX_ERROR"
    LLM_ERROR = "LLM_ERROR"
    #: 出口請求被平台政策擋下（v0.12.0 語意擴充：涵蓋 §4.2 的 HTTP proxy 與
    #: §4.4 的寄信）。HTTP 403，**不可重試** —— 政策不會因為再打一次就改變。
    #: 具體原因在 `details.reason`（枚舉 `EgressReason`）。
    EGRESS_BLOCKED = "EGRESS_BLOCKED"
    #: 政策放行了，但上游打不通（DNS、連線被拒、逾時、SMTP 連不上）。
    #: HTTP 502，可重試。與 EGRESS_BLOCKED 的分界是「該不該重試」。
    EGRESS_FAILED = "EGRESS_FAILED"
    USER_CODE_ERROR = "USER_CODE_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class EgressReason(StrEnum):
    """`EGRESS_BLOCKED` 的 `details.reason`（api-contract §4.3）。

    **錯誤 code 是公開契約、發布後要長期相容；會成長的那一維放在這裡。**
    之後加原因時不用改 `ErrorCode`。
    """

    # ── HTTP（§4.2）──────────────────────────────────────────────────
    NO_RULES = "NO_RULES"
    HOST_NOT_ALLOWED = "HOST_NOT_ALLOWED"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    PATH_NOT_ALLOWED = "PATH_NOT_ALLOWED"
    SCHEME_NOT_ALLOWED = "SCHEME_NOT_ALLOWED"
    #: **這裡沒有 HEADER_NOT_ALLOWED**（v0.12.1，U1）：沙盒自帶 header 是
    #: 「這個請求本身寫錯了」→ `422 VALIDATION_ERROR` + `details.header`，
    #: 不是「一個格式正確的請求被政策擋下」。這張表只服務 EGRESS_BLOCKED/403。
    PRIVATE_ADDRESS = "PRIVATE_ADDRESS"
    REDIRECT = "REDIRECT"
    REQUEST_TOO_LARGE = "REQUEST_TOO_LARGE"
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
    RUN_CALL_LIMIT = "RUN_CALL_LIMIT"
    # ── 寄信（§4.4）──────────────────────────────────────────────────
    SENDER_NOT_CONFIGURED = "SENDER_NOT_CONFIGURED"
    RECIPIENT_NOT_ALLOWED = "RECIPIENT_NOT_ALLOWED"
    BODY_TOO_LARGE = "BODY_TOO_LARGE"
    RUN_EMAIL_LIMIT = "RUN_EMAIL_LIMIT"
    HOURLY_EMAIL_LIMIT = "HOURLY_EMAIL_LIMIT"
    #: 密碼錯了重試一百次還是錯，而且 Gmail 會因此鎖帳號 —— 所以歸在 blocked。
    SMTP_AUTH = "SMTP_AUTH"
    TLS_REQUIRED = "TLS_REQUIRED"


class EgressKind(StrEnum):
    """`egress_calls.kind`。HTTP 與寄信共用一張稽核表（ADR-0014 M4）。"""

    HTTP = "HTTP"
    EMAIL = "EMAIL"


class EventType(StrEnum):
    """api-contract.md §1 的 SSE 事件型別枚舉。

    `tool.call` / `tool.result` / `interrupt` 已於 v0.2.0 移除，不要加回來。
    """

    RUN_STATUS = "run.status"
    NODE_START = "node.start"
    NODE_END = "node.end"
    TOKEN = "token"
    LOG = "log"
    CUSTOM = "custom"
    ERROR = "error"


#: ZEN-000 的 agent 寫死在沙盒映像裡，還沒有 Agent Registry（ZEN-001 才做）。
#: 這個固定 id 讓 `POST /v1/agents/{agent_id}/runs` 有東西可以打，
#: 其他 agent_id 一律回 NOT_FOUND —— 順便給 QA 一條真的 404 路徑可以測。
BUILTIN_AGENT_ID = "01930000-0000-7000-8000-000000000001"
BUILTIN_AGENT_VERSION_ID = "01930000-0000-7000-8000-000000000002"
BUILTIN_AGENT_SEMVER = "0.0.0-builtin"
