"""ZEN-019：per-agent 的可變設定、機密、對外出口與寄信。

依 [ADR-0014](../../../specs/adr/ADR-0014-per-agent-config-secrets-and-egress.md)。
五張表，三層模型：

  · agent_settings       L-設定：明文 JSON，會**原封不動**送進沙盒的 ctx.config
  · agent_secrets        L-機密：Fernet **密文**，值永遠不進沙盒（沙盒只拿名字）
  · agent_egress_rules   L-能力：平台願意替這個 Agent 代打哪些主機（預設拒絕）
  · agent_email_sender   L-能力：寄件者。**沒有密碼欄位**，只有 password_secret_name
  · egress_calls         稽核：HTTP 與寄信共用一張，用 kind 分

**三種機密處理在這個 schema 裡並存，不要統一**：
  · agent_secrets         Fernet 密文 —— 要解回來登入 SMTP / 打外部 API
  · agent_llm_credentials Fernet 密文（ZEN-012）
  · agent_api_keys        SHA-256 雜湊（ZEN-015）—— 只需驗證身分

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

_UUID = sa.dialects.postgresql.UUID(as_uuid=True)
_JSONB = sa.dialects.postgresql.JSONB
_TEXT_ARRAY = sa.dialects.postgresql.ARRAY(sa.Text())


def upgrade() -> None:
    # ── L-設定 ────────────────────────────────────────────────────────
    # 一個 Agent 一份 JSON 文件。**綁 Agent 不綁版本**（ADR-0014 §7，同 ADR-0011）：
    # 改一次對所有版本生效，下一筆 Run 就是新值。
    op.create_table(
        "agent_settings",
        sa.Column("agent_id", _UUID, sa.ForeignKey("agents.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("config", _JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )

    # ── L-機密 ────────────────────────────────────────────────────────
    # **寫得進去，讀不出來。** 沒有任何端點回傳 ciphertext，last_four 是唯一線索。
    op.create_table(
        "agent_secrets",
        sa.Column("agent_id", _UUID, sa.ForeignKey("agents.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("name", sa.String(64), primary_key=True),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("last_four", sa.String(8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )

    # ── L-能力：HTTP 出口白名單 ──────────────────────────────────────
    op.create_table(
        "agent_egress_rules",
        sa.Column("rule_id", _UUID, primary_key=True),
        sa.Column("agent_id", _UUID, sa.ForeignKey("agents.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("methods", _TEXT_ARRAY, nullable=False,
                  server_default=sa.text("ARRAY['GET']::text[]")),
        sa.Column("path_prefix", sa.Text()),
        # **刻意不設 FK 到 agent_secrets。** FK 會讓「刪機密」在 DB 層直接爆
        # constraint error，而契約要的是一個能說出「被誰引用」的 409（§2.7 第 6 點）。
        # 引用檢查在服務層做。
        sa.Column("auth_secret_name", sa.String(64)),
        sa.Column("auth_header", sa.String(64)),
        sa.Column("description", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        # host 在同一個 Agent 內唯一：重疊規則的優先序是 bug 的溫床，
        # 「哪一條規則生效」必須一眼看得出來（契約 §2.8 第 4 點）。
        sa.UniqueConstraint("agent_id", "host", name="uq_agent_egress_rules_agent_host"),
        sa.CheckConstraint(
            "auth_secret_name IS NULL AND auth_header IS NULL "
            "OR auth_secret_name IS NOT NULL AND auth_header IS NOT NULL",
            name="ck_agent_egress_rules_auth_pair",
        ),
        sa.CheckConstraint("array_length(methods, 1) BETWEEN 1 AND 2",
                           name="ck_agent_egress_rules_methods"),
    )
    op.create_index("ix_agent_egress_rules_agent", "agent_egress_rules", ["agent_id"])

    # ── L-能力：寄件者 ────────────────────────────────────────────────
    # **這張表沒有密碼欄位**（ADR-0014 M2）。只留一條存機密的路，
    # 開第二條就會有第二種加密方式與第二種洩漏方式。
    op.create_table(
        "agent_email_sender",
        sa.Column("agent_id", _UUID, sa.ForeignKey("agents.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("smtp_host", sa.String(253), nullable=False),
        sa.Column("smtp_port", sa.Integer(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("from_address", sa.Text(), nullable=False),
        sa.Column("password_secret_name", sa.String(64), nullable=False),
        sa.Column("allowed_recipients", _TEXT_ARRAY, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        # 非空且最多 10 個。少了它，一支迴圈寫錯的 agent 會用使用者的 Gmail
        # 帳號變成垃圾信來源，而 Google 封的是**那個帳號**（ADR-0014 M1）。
        sa.CheckConstraint("array_length(allowed_recipients, 1) BETWEEN 1 AND 10",
                           name="ck_agent_email_sender_recipients"),
        sa.CheckConstraint("smtp_port IN (25, 465, 587)",
                           name="ck_agent_email_sender_port"),
    )

    # ── 稽核 ──────────────────────────────────────────────────────────
    op.create_table(
        "egress_calls",
        sa.Column("call_id", _UUID, primary_key=True),
        sa.Column("run_id", _UUID, sa.ForeignKey("runs.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("agent_id", _UUID, sa.ForeignKey("agents.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        # **SET NULL，不是 CASCADE。** 刪一條白名單規則不該連帶抹掉它過去的
        # 稽核紀錄 —— 那正是「我改了什麼、然後發生了什麼」最需要對照的時候。
        sa.Column("rule_id", _UUID,
                  sa.ForeignKey("agent_egress_rules.rule_id", ondelete="SET NULL")),
        sa.Column("method", sa.String(8)),
        sa.Column("host", sa.String(253), nullable=False),
        # **不含 query string**（契約 §2.10 第 1 點）。query 常常就是使用者放
        # token 的地方，稽核表不該變成第二個機密外洩點。
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("status_code", sa.Integer()),
        sa.Column("request_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("response_bytes", sa.Integer(), nullable=False, server_default="0"),
        # NULL = 這次呼叫**沒有真的送出去**（被政策擋在上游之前）。
        # 每 Run 次數上限只算真的送出去的那些，否則「被擋 100 次」會讓
        # 下一次的原因變成 RUN_CALL_LIMIT，把真正的問題蓋掉。
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('HTTP','EMAIL')", name="ck_egress_calls_kind"),
    )
    op.create_index("ix_egress_calls_agent_created", "egress_calls",
                    ["agent_id", sa.text("created_at DESC")])
    op.create_index("ix_egress_calls_run", "egress_calls", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_egress_calls_run", table_name="egress_calls")
    op.drop_index("ix_egress_calls_agent_created", table_name="egress_calls")
    op.drop_table("egress_calls")
    op.drop_table("agent_email_sender")
    op.drop_index("ix_agent_egress_rules_agent", table_name="agent_egress_rules")
    op.drop_table("agent_egress_rules")
    op.drop_table("agent_secrets")
    op.drop_table("agent_settings")
