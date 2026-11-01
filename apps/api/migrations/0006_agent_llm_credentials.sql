-- ZEN-012：per-agent LLM 憑證。
--
-- 為什麼是獨立一張表，不是 agents 的一個欄位：
-- SELECT * FROM agents 出現在列表、詳情、MCP、Console 的每一條路徑上。
-- 密文放在那裡，任何一次 serializer 改動都可能把它帶出去。
-- 放獨立表，只有明確去查它的程式碼才碰得到。
--
-- agent_id 是主鍵 —— 一個 Agent 恰好一組憑證。版本不進來，
-- 因為憑證屬於 Agent：發新版不用重填，改金鑰則所有版本一起變。

CREATE TABLE agent_llm_credentials (
    agent_id UUID NOT NULL,
    ciphertext TEXT NOT NULL,
    last_four VARCHAR(8) NOT NULL,
    base_url TEXT,
    default_model VARCHAR(128),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (agent_id),
    FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE CASCADE
);

ALTER TABLE llm_calls ADD COLUMN credential_source VARCHAR(16) DEFAULT 'PLATFORM' NOT NULL;

ALTER TABLE llm_calls ADD CONSTRAINT ck_llm_calls_credential_source CHECK (credential_source IN ('PLATFORM','AGENT'));
