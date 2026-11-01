-- 平台單一 MCP 接口 /mcp：key 勾選一組 Agent，那組 Agent 的 entrypoint 就是這把 key 的 tools。
--
-- mcp_keys 和 agent_api_keys 是兩套東西：後者綁單一 Agent 的 REST；前者綁一組 Agent 的 MCP。
-- 同樣只存 SHA-256 雜湊（只需驗證身分，外洩也還原不出來）。

CREATE TABLE mcp_keys (
    id UUID NOT NULL,
    name VARCHAR(128) NOT NULL,
    key_hash VARCHAR(64) NOT NULL,
    key_prefix VARCHAR(24) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    last_used_at TIMESTAMP WITH TIME ZONE,
    revoked_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (id),
    UNIQUE (key_hash)
);

CREATE TABLE mcp_key_agents (
    mcp_key_id UUID NOT NULL,
    agent_id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (mcp_key_id, agent_id),
    FOREIGN KEY(mcp_key_id) REFERENCES mcp_keys (id) ON DELETE CASCADE,
    FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE CASCADE
);

CREATE INDEX ix_mcp_key_agents_agent ON mcp_key_agents (agent_id);

ALTER TABLE runs ADD COLUMN mcp_key_id UUID;

ALTER TABLE runs ADD FOREIGN KEY(mcp_key_id) REFERENCES mcp_keys (id) ON DELETE SET NULL;
