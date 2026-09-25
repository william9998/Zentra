"""SQLAlchemy models for the Agent registry and Run lifecycle."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from apps.api.enums import AgentSourceType, AgentVersionStatus, RunStatus, RunTrigger


class Base(DeclarativeBase):
    pass


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentVersion(Base):
    __tablename__ = "agent_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    semver: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default=AgentSourceType.OCI_IMAGE
    )
    image_ref: Mapped[str] = mapped_column(Text, nullable=False)
    image_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    version_id: Mapped[str] = mapped_column(String(12), nullable=False)
    image_platform: Mapped[str | None] = mapped_column(String(64))
    image_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    runtime_contract: Mapped[str | None] = mapped_column(String(64))
    manifest: Mapped[dict | None] = mapped_column(JSONB)
    entrypoints: Mapped[list[dict] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=AgentVersionStatus.BUILDING
    )
    error: Mapped[dict | None] = mapped_column(JSONB)
    validation_payload: Mapped[dict | None] = mapped_column(JSONB)
    build_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    builder_task_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("agent_id", "semver", name="uq_agent_versions_agent_semver"),
        CheckConstraint("source_type = 'OCI_IMAGE'", name="ck_agent_versions_source_type"),
        CheckConstraint(
            "status IN ('BUILDING','PUBLISHED','FAILED')", name="ck_agent_versions_status"
        ),
        CheckConstraint(
            "image_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_agent_versions_digest"
        ),
        CheckConstraint("version_id ~ '^[0-9a-f]{12}$'", name="ck_agent_versions_version_id"),
        Index("ix_agent_versions_agent_status_created", "agent_id", "status", created_at.desc()),
    )


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False
    )
    agent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_versions.id")
    )

    # 單租戶（ADR-0005）：欄位留著，但不做 RLS，也還沒強制執行
    owner_id: Mapped[str | None] = mapped_column(String(64))

    status: Mapped[str] = mapped_column(String(16), nullable=False, default=RunStatus.QUEUED)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False, default=RunTrigger.API)
    entrypoint: Mapped[str] = mapped_column(String(128), nullable=False, default="default")

    celery_task_id: Mapped[str | None] = mapped_column(String(64))
    sandbox_id: Mapped[str | None] = mapped_column(String(80))

    input: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    output: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[dict | None] = mapped_column(JSONB)
    usage: Mapped[dict | None] = mapped_column(JSONB)

    timeout_seconds: Mapped[int] = mapped_column(nullable=False, default=900)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_runs_agent_status_created", "agent_id", "status", created_at.desc()),
        CheckConstraint("status IN ('QUEUED','SCHEDULING','RUNNING','SUCCEEDED','FAILED','CANCELLED')", name="ck_runs_status"),
        CheckConstraint("trigger IN ('API','MCP')", name="ck_runs_trigger"),
        CheckConstraint("timeout_seconds BETWEEN 1 AND 900", name="ck_runs_timeout"),
    )


class RunEvent(Base):
    """事件的持久化真相。

    Redis Stream 只是即時匯流管道；SSE 的 `Last-Event-ID` 續傳與「Run 結束後回頭看」
    靠的是這張表（架構文件 §8）。
    """

    __tablename__ = "run_events"

    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    terminal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_run_events_run_seq", "run_id", "seq"),
        UniqueConstraint("run_id", "event_id", name="uq_run_events_run_event"),
        Index("uq_run_events_terminal", "run_id", unique=True, postgresql_where=text("terminal")),
    )


class LlmCall(Base):
    """每一次經 Internal Gateway 的 LLM 呼叫（ZEN-002 / SPEC §7.3）。

    **這張表是 LLM 用量的唯一真相。** 沙盒沒有其他出口，所以它就是全部的外網活動
    （SPEC §10 稽核要求：「所有 LLM 呼叫 100% 留存於 `llm_calls`」）。

    Run 的 `usage` 由這張表聚合而來，**不採信沙盒自行回報的數字** ——
    沙盒跑的是使用者程式碼，它報的用量沒有理由被當成帳。
    """

    __tablename__ = "llm_calls"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    input_tokens: Mapped[int] = mapped_column(nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    latency_ms: Mapped[int | None] = mapped_column()
    # 供應商回的 HTTP 狀態。失敗的呼叫也要留下來 —— 稽核要看得到「試過但失敗」。
    status_code: Mapped[int | None] = mapped_column()
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_llm_calls_run_created", "run_id", "created_at"),
    )
