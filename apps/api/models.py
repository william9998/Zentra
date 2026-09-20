"""SQLAlchemy 模型 —— 對應 docs/architecture/detailed-architecture.md §8。

ZEN-000 只建兩張表（runs / run_events）。
`agents` / `agent_versions` / `llm_calls` 留到 ZEN-001 與 ZEN-002 ——
**不預先建空表**（SPEC §11）。
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from apps.api.enums import RunStatus, RunTrigger


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    agent_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

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

    __table_args__ = (
        Index("ix_runs_agent_status_created", "agent_id", "status", created_at.desc()),
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
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_run_events_run_seq", "run_id", "seq"),)
