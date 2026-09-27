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
    EGRESS_BLOCKED = "EGRESS_BLOCKED"
    USER_CODE_ERROR = "USER_CODE_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


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
