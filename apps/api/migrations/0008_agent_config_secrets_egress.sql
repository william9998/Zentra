-- ZEN-019：per-agent 的可變設定、機密、對外出口與寄信。
--
-- 五張表，三層模型：
--   · agent_settings       L-設定：明文 JSON，會原封不動送進沙盒的 ctx.config
--   · agent_secrets        L-機密：Fernet 密文，值永遠不進沙盒（沙盒只拿名字）
--   · agent_egress_rules   L-能力：平台願意替這個 Agent 代打哪些主機（預設拒絕）
--   · agent_email_sender   L-能力：寄件者。沒有密碼欄位，只有 password_secret_name
--   · egress_calls         稽核：HTTP 與寄信共用一張，用 kind 分
--
-- 三種機密處理在這個 schema 裡並存，不要統一：
--   · agent_secrets         Fernet 密文 —— 要解回來登入 SMTP / 打外部 API
--   · agent_llm_credentials Fernet 密文（ZEN-012）
--   · agent_api_keys        SHA-256 雜湊（ZEN-015）—— 只需驗證身分

CREATE TABLE agent_settings (
    agent_id UUID NOT NULL,
    config JSONB DEFAULT '{}'::jsonb NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (agent_id),
    FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE CASCADE
);

CREATE TABLE agent_secrets (
    agent_id UUID NOT NULL,
    name VARCHAR(64) NOT NULL,
    ciphertext TEXT NOT NULL,
    last_four VARCHAR(8) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (agent_id, name),
    FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE CASCADE
);

CREATE TABLE agent_egress_rules (
    rule_id UUID NOT NULL,
    agent_id UUID NOT NULL,
    host VARCHAR(253) NOT NULL,
    methods TEXT[] DEFAULT ARRAY['GET']::text[] NOT NULL,
    path_prefix TEXT,
    auth_secret_name VARCHAR(64),
    auth_header VARCHAR(64),
    description VARCHAR(200),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (rule_id),
    CONSTRAINT uq_agent_egress_rules_agent_host UNIQUE (agent_id, host),
    CONSTRAINT ck_agent_egress_rules_auth_pair CHECK (auth_secret_name IS NULL AND auth_header IS NULL OR auth_secret_name IS NOT NULL AND auth_header IS NOT NULL),
    CONSTRAINT ck_agent_egress_rules_methods CHECK (array_length(methods, 1) BETWEEN 1 AND 2),
    FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE CASCADE
);

CREATE INDEX ix_agent_egress_rules_agent ON agent_egress_rules (agent_id);

CREATE TABLE agent_email_sender (
    agent_id UUID NOT NULL,
    smtp_host VARCHAR(253) NOT NULL,
    smtp_port INTEGER NOT NULL,
    username TEXT NOT NULL,
    from_address TEXT NOT NULL,
    password_secret_name VARCHAR(64) NOT NULL,
    allowed_recipients TEXT[] NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (agent_id),
    CONSTRAINT ck_agent_email_sender_recipients CHECK (array_length(allowed_recipients, 1) BETWEEN 1 AND 10),
    CONSTRAINT ck_agent_email_sender_port CHECK (smtp_port IN (25, 465, 587)),
    FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE CASCADE
);

CREATE TABLE egress_calls (
    call_id UUID NOT NULL,
    run_id UUID NOT NULL,
    agent_id UUID NOT NULL,
    kind VARCHAR(8) NOT NULL,
    rule_id UUID,
    method VARCHAR(8),
    host VARCHAR(253) NOT NULL,
    path TEXT NOT NULL,
    status_code INTEGER,
    request_bytes INTEGER DEFAULT '0' NOT NULL,
    response_bytes INTEGER DEFAULT '0' NOT NULL,
    latency_ms INTEGER,
    error TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (call_id),
    CONSTRAINT ck_egress_calls_kind CHECK (kind IN ('HTTP','EMAIL')),
    FOREIGN KEY(run_id) REFERENCES runs (id) ON DELETE CASCADE,
    FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE CASCADE,
    FOREIGN KEY(rule_id) REFERENCES agent_egress_rules (rule_id) ON DELETE SET NULL
);

CREATE INDEX ix_egress_calls_agent_created ON egress_calls (agent_id, created_at DESC);

CREATE INDEX ix_egress_calls_run ON egress_calls (run_id);
