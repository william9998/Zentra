"""ZEN-000：補齊 models.py 已有但 migration 缺的欄位與約束

0001 commit 之後，模型加了事件冪等（event_id 去重）、終局事件唯一性（terminal）、
以及兩個 CheckConstraint。這些是對的設計，但 migration 沒跟上 —— 一跑就 UndefinedColumn。

**教訓寫在這裡**：改 models.py 一定要同時產 migration。
ZEN-004 應該加一個 CI 檢查：alembic autogenerate 必須產出空 diff。

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- run_events：事件冪等與終局唯一 ---
    # 先給 server_default 讓既有列填得上，再拿掉 default（往後一律由應用層指定）
    op.add_column(
        "run_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False,
                  server_default=sa.text("gen_random_uuid()")),
    )
    op.alter_column("run_events", "event_id", server_default=None)

    op.add_column(
        "run_events",
        sa.Column("terminal", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("run_events", sa.Column("published_at", sa.DateTime(timezone=True)))

    # 沙盒重送同一批事件不會產生重複列
    op.create_unique_constraint("uq_run_events_run_event", "run_events", ["run_id", "event_id"])
    # 一個 Run 只能有一個終局事件 —— 由資料庫兜底，不是只靠應用層判斷
    op.create_index(
        "uq_run_events_terminal", "run_events", ["run_id"],
        unique=True, postgresql_where=sa.text("terminal"),
    )

    # --- runs：狀態與逾時的資料庫層防線 ---
    # 狀態字串不得自創（SPEC §9）；WAITING_INPUT 已於 v0.2.0 移除，這裡也不收
    op.create_check_constraint(
        "ck_runs_status", "runs",
        "status IN ('QUEUED','SCHEDULING','RUNNING','SUCCEEDED','FAILED','CANCELLED')",
    )
    # SPEC §10：Run 最長 15 分鐘硬上限
    op.create_check_constraint("ck_runs_timeout", "runs", "timeout_seconds BETWEEN 1 AND 900")


def downgrade() -> None:
    op.drop_constraint("ck_runs_timeout", "runs", type_="check")
    op.drop_constraint("ck_runs_status", "runs", type_="check")
    op.drop_index("uq_run_events_terminal", table_name="run_events")
    op.drop_constraint("uq_run_events_run_event", "run_events", type_="unique")
    op.drop_column("run_events", "published_at")
    op.drop_column("run_events", "terminal")
    op.drop_column("run_events", "event_id")
