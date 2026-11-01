-- ZEN-000：runs 與 run_events 兩張表
--
-- 只建這兩張。agents / agent_versions / llm_calls 留到後續版本
-- —— 不預先建空表（SPEC §11）。

CREATE TABLE runs (
    id UUID NOT NULL,
    agent_id UUID NOT NULL,
    agent_version_id UUID,
    owner_id VARCHAR(64),
    status VARCHAR(16) NOT NULL,
    trigger VARCHAR(16) NOT NULL,
    entrypoint VARCHAR(128) NOT NULL,
    celery_task_id VARCHAR(64),
    sandbox_id VARCHAR(80),
    input JSONB NOT NULL,
    output JSONB,
    error JSONB,
    usage JSONB,
    timeout_seconds INTEGER NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE,
    finished_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (id)
);

CREATE INDEX ix_runs_agent_status_created ON runs (agent_id, status, created_at DESC);

CREATE TABLE run_events (
    seq BIGSERIAL NOT NULL,
    run_id UUID NOT NULL,
    type VARCHAR(32) NOT NULL,
    payload JSONB NOT NULL,
    ts TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (seq),
    FOREIGN KEY(run_id) REFERENCES runs (id) ON DELETE CASCADE
);

CREATE INDEX ix_run_events_run_seq ON run_events (run_id, seq);
