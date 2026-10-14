"""ZEN-015：常駐 Agent 容器、下游 API key、用量歸戶。

依 [ADR-0012](../../../specs/adr/ADR-0012-resident-agent-container.md)：
一 Agent 一（或多）常駐容器，取代一 Run 一沙盒。

**兩張新表的機密處理刻意不同，不要統一**：
  · agent_api_keys        存**雜湊** —— 只需驗證來的人是誰，外洩也還原不出來
  · agent_llm_credentials 存**密文**（ZEN-012）—— 明文要轉發給供應商，只能加密

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_UUID = sa.dialects.postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "agent_containers",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("agent_id", _UUID, sa.ForeignKey("agents.id", ondelete="CASCADE"),
                  nullable=False),
        # 容器綁定單一版本（P5）。發新版要換容器，切換期間兩個並存。
        sa.Column("agent_version_id", _UUID, sa.ForeignKey("agent_versions.id"),
                  nullable=False),
        sa.Column("container_id", sa.String(80)),
        sa.Column("status", sa.String(16), nullable=False, server_default="STARTING"),
        # 目前跑幾個 Run。上限是 SANDBOX_RUNS_PER_CONTAINER（預設 30），滿了溢出開新容器。
        sa.Column("inflight_runs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("last_health_at", sa.DateTime(timezone=True)),
        # 一直重啟的容器是壞掉了，不是需要再重啟一次 —— 這個數字讓那件事看得見。
        sa.Column("restart_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('STARTING','READY','UNHEALTHY','STOPPED','FAILED')",
            name="ck_agent_containers_status",
        ),
        sa.CheckConstraint("inflight_runs >= 0", name="ck_agent_containers_inflight"),
        sa.CheckConstraint("restart_count >= 0", name="ck_agent_containers_restarts"),
    )
    # 派工時要找「這個 Agent 還有空位的 READY 容器」，這條索引就是為那個查詢
    op.create_index("ix_agent_containers_agent_status", "agent_containers",
                    ["agent_id", "status"])

    op.create_table(
        "agent_api_keys",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("agent_id", _UUID, sa.ForeignKey("agents.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        # **雜湊，不是密文。** 平台只需要驗證，不需要還原。
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        # 顯示用（zk_live_a1b2…）。讓人在列表裡認得出是哪一把。
        sa.Column("key_prefix", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        # 軟撤銷：保留歷史用量的歸屬。硬刪會讓過去的帳變成無主。
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_agent_api_keys_agent", "agent_api_keys", ["agent_id"])

    # runs 的三個新欄位。llm_calls 不動 —— 它已有 run_id，join 就拿得到 key 的歸屬，
    # 再存一次 api_key_id 會有兩份可能不一致的真相。
    op.add_column("runs", sa.Column(
        "api_key_id", _UUID, sa.ForeignKey("agent_api_keys.id", ondelete="SET NULL")))
    op.add_column("runs", sa.Column("session_id", _UUID))
    op.add_column("runs", sa.Column(
        "container_id", _UUID, sa.ForeignKey("agent_containers.id", ondelete="SET NULL")))

    # 除錯查詢的主要形狀：某把 key 在某段時間內的請求
    op.create_index("ix_runs_api_key_created", "runs", ["api_key_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_runs_api_key_created", table_name="runs")
    op.drop_column("runs", "container_id")
    op.drop_column("runs", "session_id")
    op.drop_column("runs", "api_key_id")
    op.drop_index("ix_agent_api_keys_agent", table_name="agent_api_keys")
    op.drop_table("agent_api_keys")
    op.drop_index("ix_agent_containers_agent_status", table_name="agent_containers")
    op.drop_table("agent_containers")
