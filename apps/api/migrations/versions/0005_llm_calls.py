"""ZEN-002: llm_calls —— LLM 用量的唯一真相。

沙盒沒有其他對外出口，所以這張表就是全部的外網活動（SPEC §10 稽核要求）。
Run 的 `usage` 由它聚合，不採信沙盒自行回報的數字。

失敗的呼叫也留下來（`status_code` / `error`）—— 稽核要看得到「試過但失敗」。

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id", sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("status_code", sa.Integer()),
        sa.Column("error", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.CheckConstraint("input_tokens >= 0", name="ck_llm_calls_input_tokens"),
        sa.CheckConstraint("output_tokens >= 0", name="ck_llm_calls_output_tokens"),
        sa.CheckConstraint("cost_usd >= 0", name="ck_llm_calls_cost"),
    )
    op.create_index("ix_llm_calls_run_created", "llm_calls", ["run_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_llm_calls_run_created", table_name="llm_calls")
    op.drop_table("llm_calls")
