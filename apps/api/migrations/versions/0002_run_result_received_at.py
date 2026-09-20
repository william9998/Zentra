"""ZEN-000：runs 補 result_received_at

`services/runs.py` 的 save_result 用這個欄位做結果冪等
（沙盒重送同一份結果要接受，送不一樣的要擋）。
models.py 有、0001 沒建 —— 這是 0001 commit 之後才加的欄位，
所以補一個 0002，不改已提交的 0001（別人可能已經套用過）。

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("result_received_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("runs", "result_received_at")
