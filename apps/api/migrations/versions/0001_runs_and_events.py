"""ZEN-000：runs 與 run_events 兩張表

只建這兩張。agents / agent_versions / llm_calls 留到 ZEN-001 與 ZEN-002
—— **不預先建空表**（SPEC §11）。

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_version_id", postgresql.UUID(as_uuid=True)),
        # 單租戶（ADR-0005）：欄位留著，不做 RLS
        sa.Column("owner_id", sa.String(64)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("entrypoint", sa.String(128), nullable=False),
        sa.Column("celery_task_id", sa.String(64)),
        sa.Column("sandbox_id", sa.String(80)),
        sa.Column("input", postgresql.JSONB, nullable=False),
        sa.Column("output", postgresql.JSONB),
        sa.Column("error", postgresql.JSONB),
        sa.Column("usage", postgresql.JSONB),
        sa.Column("timeout_seconds", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_runs_agent_status_created", "runs", ["agent_id", "status", sa.text("created_at DESC")]
    )

    op.create_table(
        "run_events",
        sa.Column("seq", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_run_events_run_seq", "run_events", ["run_id", "seq"])


def downgrade() -> None:
    op.drop_table("run_events")
    op.drop_table("runs")
