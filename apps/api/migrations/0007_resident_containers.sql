-- ZEN-015：常駐 Agent 容器、下游 API key、用量歸戶。
--
-- 一 Agent 一（或多）常駐容器，取代一 Run 一沙盒。
--
-- 兩張新表的機密處理刻意不同，不要統一：
--   · agent_api_keys        存雜湊 —— 只需驗證來的人是誰，外洩也還原不出來
--   · agent_llm_credentials 存密文（ZEN-012）—— 明文要轉發給供應商，只能加密

CREATE TABLE agent_containers (
    id UUID NOT NULL,
    agent_id UUID NOT NULL,
    agent_version_id UUID NOT NULL,
    container_id VARCHAR(80),
    status VARCHAR(16) DEFAULT 'STARTING' NOT NULL,
    inflight_runs INTEGER DEFAULT '0' NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE,
    last_health_at TIMESTAMP WITH TIME ZONE,
    restart_count INTEGER DEFAULT '0' NOT NULL,
    error TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_agent_containers_status CHECK (status IN ('STARTING','READY','UNHEALTHY','STOPPED','FAILED')),
    CONSTRAINT ck_agent_containers_inflight CHECK (inflight_runs >= 0),
    CONSTRAINT ck_agent_containers_restarts CHECK (restart_count >= 0),
    FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE CASCADE,
    FOREIGN KEY(agent_version_id) REFERENCES agent_versions (id)
);

CREATE INDEX ix_agent_containers_agent_status ON agent_containers (agent_id, status);

CREATE TABLE agent_api_keys (
    id UUID NOT NULL,
    agent_id UUID NOT NULL,
    name VARCHAR(128) NOT NULL,
    key_hash VARCHAR(64) NOT NULL,
    key_prefix VARCHAR(24) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    last_used_at TIMESTAMP WITH TIME ZONE,
    revoked_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (id),
    FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE CASCADE,
    UNIQUE (key_hash)
);

CREATE INDEX ix_agent_api_keys_agent ON agent_api_keys (agent_id);

ALTER TABLE runs ADD COLUMN api_key_id UUID;

ALTER TABLE runs ADD FOREIGN KEY(api_key_id) REFERENCES agent_api_keys (id) ON DELETE SET NULL;

ALTER TABLE runs ADD COLUMN session_id UUID;

ALTER TABLE runs ADD COLUMN container_id UUID;

ALTER TABLE runs ADD FOREIGN KEY(container_id) REFERENCES agent_containers (id) ON DELETE SET NULL;

CREATE INDEX ix_runs_api_key_created ON runs (api_key_id, created_at);
