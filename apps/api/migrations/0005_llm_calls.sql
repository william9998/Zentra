-- ZEN-002: llm_calls —— LLM 用量的唯一真相。
--
-- 沙盒沒有其他對外出口，所以這張表就是全部的外網活動（SPEC §10 稽核要求）。
-- Run 的 usage 由它聚合，不採信沙盒自行回報的數字。
-- 失敗的呼叫也留下來（status_code / error）—— 稽核要看得到「試過但失敗」。

CREATE TABLE llm_calls (
    id UUID NOT NULL,
    run_id UUID NOT NULL,
    model VARCHAR(128) NOT NULL,
    input_tokens INTEGER DEFAULT '0' NOT NULL,
    output_tokens INTEGER DEFAULT '0' NOT NULL,
    cost_usd NUMERIC(12, 6) DEFAULT '0' NOT NULL,
    latency_ms INTEGER,
    status_code INTEGER,
    error TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_llm_calls_input_tokens CHECK (input_tokens >= 0),
    CONSTRAINT ck_llm_calls_output_tokens CHECK (output_tokens >= 0),
    CONSTRAINT ck_llm_calls_cost CHECK (cost_usd >= 0),
    FOREIGN KEY(run_id) REFERENCES runs (id) ON DELETE CASCADE
);

CREATE INDEX ix_llm_calls_run_created ON llm_calls (run_id, created_at);
