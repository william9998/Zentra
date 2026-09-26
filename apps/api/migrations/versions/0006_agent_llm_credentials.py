"""ZEN-012：per-agent LLM 憑證。

**為什麼是獨立一張表，不是 `agents` 的一個欄位**：
`SELECT * FROM agents` 出現在列表、詳情、MCP、Console 的每一條路徑上。
密文放在那裡，任何一次 serializer 改動都可能把它帶出去。
放獨立表，只有明確去查它的程式碼才碰得到。

**agent_id 是主鍵** —— 一個 Agent 恰好一組憑證。版本不進來，
因為憑證屬於 Agent：發新版不用重填，改金鑰則所有版本一起變（這是需求，見 ADR-0011）。

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_llm_credentials",
        sa.Column(
            "agent_id", sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True,
        ),
        # Fernet token。平台永遠不回傳它，也沒有任何端點讀得出明文。
        sa.Column("ciphertext", sa.Text(), nullable=False),
        # 只給 UI 顯示「設定的是哪一把」用。不足四碼時存等長遮罩。
        sa.Column("last_four", sa.String(8), nullable=False),
        # 金鑰與端點是一組的：Anthropic 的金鑰打 OpenAI 的 base_url 不會動。
        sa.Column("base_url", sa.Text()),
        sa.Column("default_model", sa.String(128)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )

    # 稽核要分得出這次呼叫用的是誰的金鑰。憑證可變而版本不可變（P5），
    # 所以「同一個 agent@1.0.0 昨天今天行為不同」是可能的 —— 至少要留下痕跡。
    op.add_column(
        "llm_calls",
        sa.Column("credential_source", sa.String(16), nullable=False, server_default="PLATFORM"),
    )
    op.create_check_constraint(
        "ck_llm_calls_credential_source", "llm_calls",
        "credential_source IN ('PLATFORM','AGENT')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_llm_calls_credential_source", "llm_calls", type_="check")
    op.drop_column("llm_calls", "credential_source")
    op.drop_table("agent_llm_credentials")
