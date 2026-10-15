"""每張表一個 frozen dataclass。純資料，不 import 任何 driver。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar, Mapping, Self


class RowMixin:
    COLUMNS: ClassVar[tuple[str, ...]]
    TABLE: ClassVar[str]

    @classmethod
    def from_row(cls, mapping: Mapping[str, Any]) -> Self:
        return cls(**{name: mapping[name] for name in cls.COLUMNS})

    @classmethod
    def returning(cls) -> str:
        return ", ".join(cls.COLUMNS)


@dataclass(frozen=True, slots=True)
class Agent(RowMixin):
    TABLE: ClassVar[str] = "agents"
    COLUMNS: ClassVar[tuple[str, ...]] = (
        "id", "owner_id", "name", "description",
        "created_at", "updated_at", "deleted_at",
    )
    id: uuid.UUID
    owner_id: uuid.UUID | None
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


@dataclass(frozen=True, slots=True)
class AgentVersion(RowMixin):
    TABLE: ClassVar[str] = "agent_versions"
    COLUMNS: ClassVar[tuple[str, ...]] = (
        "id", "agent_id", "semver", "source_type", "image_ref", "image_digest",
        "version_id", "image_platform", "image_size_bytes", "runtime_contract",
        "manifest", "entrypoints", "status", "error", "validation_payload",
        "build_claimed_at", "builder_task_id", "created_at", "published_at",
        "superseded_at",
    )
    id: uuid.UUID
    agent_id: uuid.UUID
    semver: str
    source_type: str
    image_ref: str
    image_digest: str
    version_id: str
    image_platform: str | None
    image_size_bytes: int | None
    runtime_contract: str | None
    manifest: Any
    entrypoints: Any
    status: str
    error: Any
    validation_payload: Any
    build_claimed_at: datetime | None
    builder_task_id: str | None
    created_at: datetime
    published_at: datetime | None
    superseded_at: datetime | None


@dataclass(frozen=True, slots=True)
class Run(RowMixin):
    TABLE: ClassVar[str] = "runs"
    COLUMNS: ClassVar[tuple[str, ...]] = (
        "id", "agent_id", "agent_version_id", "owner_id", "status", "trigger",
        "entrypoint", "celery_task_id", "sandbox_id", "input", "output", "error",
        "usage", "timeout_seconds", "api_key_id", "session_id", "container_id",
        "created_at", "started_at", "finished_at", "result_received_at",
    )
    id: uuid.UUID
    agent_id: uuid.UUID
    agent_version_id: uuid.UUID | None
    owner_id: str | None
    status: str
    trigger: str
    entrypoint: str
    celery_task_id: str | None
    sandbox_id: str | None
    input: Any
    output: Any
    error: Any
    usage: Any
    timeout_seconds: int
    api_key_id: uuid.UUID | None
    session_id: uuid.UUID | None
    container_id: uuid.UUID | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    result_received_at: datetime | None


@dataclass(frozen=True, slots=True)
class RunEvent(RowMixin):
    TABLE: ClassVar[str] = "run_events"
    COLUMNS: ClassVar[tuple[str, ...]] = (
        "seq", "run_id", "type", "event_id", "terminal",
        "published_at", "payload", "ts",
    )
    seq: int
    run_id: uuid.UUID
    type: str
    event_id: uuid.UUID
    terminal: bool
    published_at: datetime | None
    payload: Any
    ts: datetime


@dataclass(frozen=True, slots=True)
class LlmCall(RowMixin):
    TABLE: ClassVar[str] = "llm_calls"
    COLUMNS: ClassVar[tuple[str, ...]] = (
        "id", "run_id", "model", "input_tokens", "output_tokens", "cost_usd",
        "latency_ms", "status_code", "error", "credential_source", "created_at",
    )
    id: uuid.UUID
    run_id: uuid.UUID
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    latency_ms: int | None
    status_code: int | None
    error: str | None
    credential_source: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AgentLlmCredential(RowMixin):
    TABLE: ClassVar[str] = "agent_llm_credentials"
    COLUMNS: ClassVar[tuple[str, ...]] = (
        "agent_id", "ciphertext", "last_four", "base_url", "default_model",
        "created_at", "updated_at",
    )
    agent_id: uuid.UUID
    ciphertext: str
    last_four: str
    base_url: str | None
    default_model: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AgentContainer(RowMixin):
    TABLE: ClassVar[str] = "agent_containers"
    COLUMNS: ClassVar[tuple[str, ...]] = (
        "id", "agent_id", "agent_version_id", "container_id", "status",
        "inflight_runs", "started_at", "last_health_at", "restart_count",
        "error", "created_at",
    )
    id: uuid.UUID
    agent_id: uuid.UUID
    agent_version_id: uuid.UUID
    container_id: str | None
    status: str
    inflight_runs: int
    started_at: datetime | None
    last_health_at: datetime | None
    restart_count: int
    error: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AgentApiKey(RowMixin):
    TABLE: ClassVar[str] = "agent_api_keys"
    COLUMNS: ClassVar[tuple[str, ...]] = (
        "id", "agent_id", "name", "key_hash", "key_prefix",
        "created_at", "last_used_at", "revoked_at",
    )
    id: uuid.UUID
    agent_id: uuid.UUID
    name: str
    key_hash: str
    key_prefix: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class AgentSettings(RowMixin):
    TABLE: ClassVar[str] = "agent_settings"
    COLUMNS: ClassVar[tuple[str, ...]] = ("agent_id", "config", "created_at", "updated_at")
    agent_id: uuid.UUID
    config: Any
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AgentSecret(RowMixin):
    TABLE: ClassVar[str] = "agent_secrets"
    COLUMNS: ClassVar[tuple[str, ...]] = (
        "agent_id", "name", "ciphertext", "last_four", "created_at", "updated_at",
    )
    agent_id: uuid.UUID
    name: str
    ciphertext: str
    last_four: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AgentEgressRule(RowMixin):
    TABLE: ClassVar[str] = "agent_egress_rules"
    COLUMNS: ClassVar[tuple[str, ...]] = (
        "rule_id", "agent_id", "host", "methods", "path_prefix",
        "auth_secret_name", "auth_header", "description", "created_at", "updated_at",
    )
    rule_id: uuid.UUID
    agent_id: uuid.UUID
    host: str
    methods: list[str]
    path_prefix: str | None
    auth_secret_name: str | None
    auth_header: str | None
    description: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AgentEmailSender(RowMixin):
    TABLE: ClassVar[str] = "agent_email_sender"
    COLUMNS: ClassVar[tuple[str, ...]] = (
        "agent_id", "smtp_host", "smtp_port", "username", "from_address",
        "password_secret_name", "allowed_recipients", "created_at", "updated_at",
    )
    agent_id: uuid.UUID
    smtp_host: str
    smtp_port: int
    username: str
    from_address: str
    password_secret_name: str
    allowed_recipients: list[str]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class EgressCall(RowMixin):
    TABLE: ClassVar[str] = "egress_calls"
    COLUMNS: ClassVar[tuple[str, ...]] = (
        "call_id", "run_id", "agent_id", "kind", "rule_id", "method", "host",
        "path", "status_code", "request_bytes", "response_bytes", "latency_ms",
        "error", "created_at",
    )
    call_id: uuid.UUID
    run_id: uuid.UUID
    agent_id: uuid.UUID
    kind: str
    rule_id: uuid.UUID | None
    method: str | None
    host: str
    path: str
    status_code: int | None
    request_bytes: int
    response_bytes: int
    latency_ms: int | None
    error: str | None
    created_at: datetime


ALL_ROWS: tuple[type[RowMixin], ...] = (
    Agent, AgentVersion, Run, RunEvent, LlmCall, AgentLlmCredential,
    AgentContainer, AgentApiKey, AgentSettings, AgentSecret,
    AgentEgressRule, AgentEmailSender, EgressCall,
)
