-- ZEN-008: OCI Agent registry and immutable versions.
--
-- builtin-echo fixture 的 INSERT 必須照抄，含 agent id
-- 01930000-0000-7000-8000-000000000001。JSON 以字面值寫入，不要當綁定參數。

CREATE TABLE agents (
    id UUID NOT NULL,
    owner_id UUID,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    deleted_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (id),
    UNIQUE (name)
);

CREATE TABLE agent_versions (
    id UUID NOT NULL,
    agent_id UUID NOT NULL,
    semver VARCHAR(64) NOT NULL,
    source_type VARCHAR(16) NOT NULL,
    image_ref TEXT NOT NULL,
    image_digest VARCHAR(71) NOT NULL,
    version_id VARCHAR(12) NOT NULL,
    image_platform VARCHAR(64),
    image_size_bytes BIGINT,
    runtime_contract VARCHAR(64),
    manifest JSONB,
    entrypoints JSONB,
    status VARCHAR(16) NOT NULL,
    error JSONB,
    validation_payload JSONB,
    build_claimed_at TIMESTAMP WITH TIME ZONE,
    builder_task_id VARCHAR(64),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    published_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (id),
    CONSTRAINT uq_agent_versions_agent_semver UNIQUE (agent_id, semver),
    CONSTRAINT ck_agent_versions_source_type CHECK (source_type = 'OCI_IMAGE'),
    CONSTRAINT ck_agent_versions_status CHECK (status IN ('BUILDING','PUBLISHED','FAILED')),
    CONSTRAINT ck_agent_versions_digest CHECK (image_digest ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT ck_agent_versions_version_id CHECK (version_id ~ '^[0-9a-f]{12}$'),
    FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE CASCADE
);

CREATE INDEX ix_agent_versions_agent_status_created ON agent_versions (agent_id, status, created_at DESC);

INSERT INTO agents (id, name, description) VALUES (CAST('01930000-0000-7000-8000-000000000001' AS uuid), 'builtin-echo', 'ZEN-000 compatibility fixture') ON CONFLICT (id) DO NOTHING;

INSERT INTO agent_versions (id, agent_id, semver, source_type, image_ref, image_digest, version_id,  image_platform, runtime_contract, manifest, entrypoints, status, published_at) VALUES (CAST('01930000-0000-7000-8000-000000000002' AS uuid), CAST('01930000-0000-7000-8000-000000000001' AS uuid), '0.0.0-builtin', 'OCI_IMAGE', 'zentra.local/builtin@sha256:0000000000000000000000000000000000000000000000000000000000000000', 'sha256:0000000000000000000000000000000000000000000000000000000000000000',  '000000000000', 'linux/amd64', 'zentra.image/v1',  CAST('{"agent_version": "0.0.0-builtin", "builtin": true}' AS jsonb),  CAST('[{"name": "default", "description": "Echo a question", "input_schema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}, "output_schema": {"type": "object", "properties": {"echo": {"type": "string"}}, "required": ["echo"]}}]' AS jsonb),  'PUBLISHED', now()) ON CONFLICT (id) DO NOTHING;

ALTER TABLE runs ADD CONSTRAINT fk_runs_agent FOREIGN KEY(agent_id) REFERENCES agents (id);

ALTER TABLE runs ADD CONSTRAINT fk_runs_agent_version FOREIGN KEY(agent_version_id) REFERENCES agent_versions (id);

ALTER TABLE runs ADD CONSTRAINT ck_runs_trigger CHECK (trigger IN ('API','MCP'));
