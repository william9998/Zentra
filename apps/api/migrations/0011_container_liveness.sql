-- 常駐容器的存活檢查（specs/container-orchestration.md §存活檢查）。
--
-- 容器死掉時 worker 會自動重啟，但 10 分鐘內最多 3 次，第 4 次就放棄。
-- `restart_count` 是累計總數，看不出「最近 10 分鐘」，所以另外記一輪的起點與次數。

ALTER TABLE agent_containers ADD COLUMN restart_window_started_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE agent_containers ADD COLUMN restarts_in_window INTEGER DEFAULT '0' NOT NULL;
ALTER TABLE agent_containers ADD CONSTRAINT ck_agent_containers_restarts_in_window
    CHECK (restarts_in_window >= 0);

-- 最後一次死因。不放 `error`：重啟成功時 `error` 會被清空，死因就跟著不見了。
ALTER TABLE agent_containers ADD COLUMN last_died_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE agent_containers ADD COLUMN last_death_reason TEXT;
