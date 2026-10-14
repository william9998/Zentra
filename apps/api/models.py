"""SQLAlchemy models for the Agent registry and Run lifecycle."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
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

    # ── ZEN-015 ──────────────────────────────────────────────────────
    #: 哪一把下游 API key 觸發的。Console 或內部發起時是 None。
    #: 用量歸戶靠它 join `llm_calls`（那張表刻意不重複存一份）。
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_api_keys.id", ondelete="SET NULL")
    )
    #: 多輪對話用，**本期不啟用**。先建欄位是因為之後補 migration 比現在多一欄貴。
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    #: 跑在哪個常駐容器。除錯時要回答「這批錯誤是不是同一個容器出的」。
    container_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_containers.id", ondelete="SET NULL")
    )

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
    #: 這次用的是 Agent 自己的金鑰還是平台預設的。憑證可變、版本不可變（P5），
    #: 所以要留得下「那一次到底用了誰的金鑰」。
    credential_source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="PLATFORM", server_default=text("'PLATFORM'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_llm_calls_run_created", "run_id", "created_at"),
    )


class AgentLlmCredential(Base):
    """一個 Agent 的 LLM 憑證（ZEN-012）。

    **綁 Agent 不綁版本。** 發新版本不用重填，改金鑰則該 Agent 的所有版本一起變 ——
    這是刻意的需求，代價與 P5 的互動記在 [ADR-0011]。

    **密文只出得去一個地方**：Internal Gateway 換金鑰的那一行。
    沒有任何端點讀得出明文，`last_four` 是 UI 唯一看得到的線索。
    """

    __tablename__ = "agent_llm_credentials"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True
    )
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    last_four: Mapped[str] = mapped_column(String(8), nullable=False)
    base_url: Mapped[str | None] = mapped_column(Text)
    default_model: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AgentContainer(Base):
    """一個 Agent 的常駐容器（ZEN-015 / ADR-0012）。

    取代「一 Run 一沙盒」：容器啟動後常駐，Run 被派進去非同步並行處理。
    一個 Agent 可以有**多個**容器 —— 單一容器的並發到 `SANDBOX_RUNS_PER_CONTAINER`
    （預設 30）就溢出開新的，讓容器掛掉時的爆炸半徑有界。

    **容器綁定單一 `agent_version_id`**（P5）。發新版要換容器，切換期間兩個並存。
    """

    __tablename__ = "agent_containers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    agent_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_versions.id"), nullable=False
    )
    container_id: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="STARTING")
    inflight_runs: Mapped[int] = mapped_column(nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_health_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: 一直重啟的容器是壞掉了，不是需要再重啟一次。這個數字讓那件事看得見。
    restart_count: Mapped[int] = mapped_column(nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_agent_containers_agent_status", "agent_id", "status"),
        CheckConstraint(
            "status IN ('STARTING','READY','UNHEALTHY','STOPPED','FAILED')",
            name="ck_agent_containers_status",
        ),
    )


class AgentApiKey(Base):
    """下游應用拿來呼叫這個 Agent 的 key（ZEN-015）。

    **存雜湊，不是密文** —— 這裡只需要「驗證來的人是誰」，雜湊就夠且更安全：
    資料庫外洩也還原不出可用的 key。

    與 `AgentLlmCredential` 的差別是刻意的：那個要把明文轉發給供應商，
    只能加密不能雜湊。**兩者不要做成同一套。**
    """

    __tablename__ = "agent_api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    key_prefix: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: 軟撤銷：保留歷史用量的歸屬。硬刪會讓過去的帳變成無主。
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_agent_api_keys_agent", "agent_id"),)


# ── ZEN-019：per-agent 的設定、機密與對外能力（ADR-0014）───────────────
#
# 三層都**綁 Agent 不綁版本**，刻意對所有版本生效。判準「誰的責任」沿用
# ADR-0011：image 是開發者的交付物，設定與憑證是維運設定。


class AgentSettings(Base):
    """一個 Agent 的可變設定（ZEN-019 / api-contract §2.6）。

    **這份 JSON 會原封不動送進沙盒的 `ctx.config`。** 它不是機密：
    `GET` 回明文，Console 上看得到。API key 與密碼要放 `AgentSecret`。

    只有整份取代，沒有 PATCH —— 兩種寫入語意等於兩條要測、要寫文件的路徑。
    """

    __tablename__ = "agent_settings"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True
    )
    config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AgentSecret(Base):
    """一個 Agent 的具名機密（ZEN-019 / api-contract §2.7）。

    **寫得進去，讀不出來。** 沒有任何端點回傳明文或密文 —— 與
    `AgentLlmCredential` 同一條規則，同一個理由：靠「沒有那個功能」保證。

    **沙盒只拿得到名字**（`secret_names`）。值由 Gateway 在代打 HTTP／寄信時
    當場解密注入，從頭到尾沒進過沙盒（P4）。

    名字長得像環境變數，但它永遠不會變成環境變數 —— 沙盒的 env 是精確集合檢查。
    """

    __tablename__ = "agent_secrets"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    last_four: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AgentEgressRule(Base):
    """平台願意替這個 Agent 代打哪些主機（ZEN-019 / api-contract §2.8）。

    **預設拒絕，沒有萬用字元。** 一條規則都沒有 → 一個外部主機都連不到。
    白名單的價值全部來自這件事（ADR-0014 E1）。

    `auth_secret_name` / `auth_header` 成對出現時，平台在代打前解密那個機密
    並加上該 header —— 沙盒不得自帶認證（E3）。
    """

    __tablename__ = "agent_egress_rules"

    rule_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    host: Mapped[str] = mapped_column(String(253), nullable=False)
    methods: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("ARRAY['GET']::text[]")
    )
    path_prefix: Mapped[str | None] = mapped_column(Text)
    #: 指向 `AgentSecret.name`，**刻意沒有 FK**（spec §5.1 第 3 點）：
    #: 契約要的是能說出「被誰引用」的 409，不是 DB constraint error。
    auth_secret_name: Mapped[str | None] = mapped_column(String(64))
    auth_header: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("agent_id", "host", name="uq_agent_egress_rules_agent_host"),
        Index("ix_agent_egress_rules_agent", "agent_id"),
    )


class AgentEmailSender(Base):
    """這個 Agent 用哪個信箱寄信（ZEN-019 / api-contract §2.9）。

    **這張表沒有密碼欄位**（ADR-0014 M2）。密碼只能以 `password_secret_name`
    指名一個 `AgentSecret`。

    `allowed_recipients` 必填非空：少了它，一支迴圈寫錯的 agent 會用使用者的
    Gmail 帳號變成垃圾信來源，而 Google 封的是**那個帳號**。
    """

    __tablename__ = "agent_email_sender"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True
    )
    smtp_host: Mapped[str] = mapped_column(String(253), nullable=False)
    smtp_port: Mapped[int] = mapped_column(nullable=False)
    username: Mapped[str] = mapped_column(Text, nullable=False)
    from_address: Mapped[str] = mapped_column(Text, nullable=False)
    password_secret_name: Mapped[str] = mapped_column(String(64), nullable=False)
    allowed_recipients: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EgressCall(Base):
    """每一次對外出口的稽核（ZEN-019 / api-contract §2.10，比照 `LlmCall`）。

    **HTTP 與寄信在同一張表**，用 `kind` 分：「這個 Agent 往外送了什麼」是
    一個問題，不該有兩個要分別查的地方（ADR-0014 M4）。

    **被擋下的呼叫也要寫一列** —— 只記成功的話，「為什麼我的 agent 連不出去」
    在畫面上完全沒有線索。
    """

    __tablename__ = "egress_calls"

    call_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    #: **SET NULL，不是 CASCADE。** 刪規則不該抹掉稽核對照。
    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_egress_rules.rule_id", ondelete="SET NULL"),
    )
    method: Mapped[str | None] = mapped_column(String(8))
    host: Mapped[str] = mapped_column(String(253), nullable=False)
    #: **不含 query string。** kind=EMAIL 時放收件人（以 `, ` 串接）。
    path: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[int | None] = mapped_column()
    request_bytes: Mapped[int] = mapped_column(nullable=False, default=0)
    response_bytes: Mapped[int] = mapped_column(nullable=False, default=0)
    #: NULL = 政策擋在送出之前。每 Run 的次數上限只算真的送出去的那些。
    latency_ms: Mapped[int | None] = mapped_column()
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_egress_calls_agent_created", "agent_id", created_at.desc()),
        Index("ix_egress_calls_run", "run_id"),
    )
