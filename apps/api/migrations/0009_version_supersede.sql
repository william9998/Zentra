-- ADR-0016：同一個版號重新註冊 = 取代那個版本。
--
-- 同一個 (agent_id, semver) 現在可以有很多列：
--   · 至多一筆 PUBLISHED   —— 現役，Run 與 MCP 用它
--   · 至多一筆 BUILDING    —— 正在驗證的取代者；第二個同時送來的回 409
--   · 任意筆 SUPERSEDED    —— 被取代的舊紀錄，舊 Run 靠它查得到當時跑的 digest
--   · 任意筆 FAILED        —— 驗證沒過的嘗試
--
-- 原本的 UNIQUE (agent_id, semver) 擋掉了整件事，改成兩個 partial unique index。

ALTER TABLE agent_versions DROP CONSTRAINT uq_agent_versions_agent_semver;
ALTER TABLE agent_versions DROP CONSTRAINT ck_agent_versions_status;
ALTER TABLE agent_versions ADD CONSTRAINT ck_agent_versions_status
    CHECK (status IN ('BUILDING','PUBLISHED','FAILED','SUPERSEDED'));

CREATE UNIQUE INDEX uq_agent_versions_published_semver
    ON agent_versions (agent_id, semver) WHERE status = 'PUBLISHED';
CREATE UNIQUE INDEX uq_agent_versions_building_semver
    ON agent_versions (agent_id, semver) WHERE status = 'BUILDING';

-- 取代的時間點。舊 image 何時可以清、畫面上要顯示「何時被取代」都靠它。
ALTER TABLE agent_versions ADD COLUMN superseded_at TIMESTAMP WITH TIME ZONE;
