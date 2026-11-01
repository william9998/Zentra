-- ZEN-000：補齊模型已有但 0001／0002 缺的欄位與約束
--
-- 0001 之後模型加了事件冪等（event_id 去重）、終局事件唯一性（terminal）、
-- 以及兩個 CheckConstraint。這些是對的設計，但當時的 migration 沒跟上。
--
-- 先給 gen_random_uuid() default 讓既有列有值，再拿掉 default（往後一律由應用層指定）。

ALTER TABLE run_events ADD COLUMN event_id UUID DEFAULT gen_random_uuid() NOT NULL;

ALTER TABLE run_events ALTER COLUMN event_id DROP DEFAULT;

ALTER TABLE run_events ADD COLUMN terminal BOOLEAN DEFAULT false NOT NULL;

ALTER TABLE run_events ADD COLUMN published_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE run_events ADD CONSTRAINT uq_run_events_run_event UNIQUE (run_id, event_id);

CREATE UNIQUE INDEX uq_run_events_terminal ON run_events (run_id) WHERE terminal;

ALTER TABLE runs ADD CONSTRAINT ck_runs_status CHECK (status IN ('QUEUED','SCHEDULING','RUNNING','SUCCEEDED','FAILED','CANCELLED'));

ALTER TABLE runs ADD CONSTRAINT ck_runs_timeout CHECK (timeout_seconds BETWEEN 1 AND 900);
