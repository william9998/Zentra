-- 退休 ZEN-000 的內建測試 Agent `builtin-echo`（0004 種進來的那兩列）。
--
-- 它跑的是寫死在平台裡的 tag 映像（zentra/agent-runtime:dev）和全 0 的假 digest，
-- 派工也為它開了特例、永遠不走常駐容器 —— 所以拿它測，測不到使用者真正走的路。
-- 取代它的是 examples/probe-agent，走正式的 push → 驗證 → 發布（scripts/fixture.sh）。
--
-- 刪除順序照 services/agents.py::delete_agent：runs 與 agent_containers 對版本**沒有** CASCADE，
-- 要由下往上刪。run_events / llm_calls 跟著 runs 走，api key、LLM 憑證、MCP 勾選跟著 agents 走。
-- 0004 不改：migration 是 schema 的歷史，空庫照樣先種再刪。

DELETE FROM egress_calls       WHERE agent_id = '01930000-0000-7000-8000-000000000001';
DELETE FROM agent_egress_rules WHERE agent_id = '01930000-0000-7000-8000-000000000001';
DELETE FROM agent_email_sender WHERE agent_id = '01930000-0000-7000-8000-000000000001';
DELETE FROM agent_secrets      WHERE agent_id = '01930000-0000-7000-8000-000000000001';
DELETE FROM agent_settings     WHERE agent_id = '01930000-0000-7000-8000-000000000001';
DELETE FROM runs               WHERE agent_id = '01930000-0000-7000-8000-000000000001';
DELETE FROM agent_containers   WHERE agent_id = '01930000-0000-7000-8000-000000000001';
DELETE FROM agent_versions     WHERE agent_id = '01930000-0000-7000-8000-000000000001';
DELETE FROM agents             WHERE id       = '01930000-0000-7000-8000-000000000001';
