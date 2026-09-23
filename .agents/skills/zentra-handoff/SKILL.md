---
name: zentra-handoff
description: 依模板產出 Zentra 交接單。只有跨 session 的大功能需要，小改動不用寫。
---

# zentra-handoff

流程的唯一版本在 [`.claude/commands/handoff.md`](../../../.claude/commands/handoff.md) ——
Claude Code 與 Codex 共用同一份，這裡不再複製（複製就會有兩份各自漂移的流程）。

**照那份做。** 角色名稱用 `.codex/agents/` 註冊的 `architect` / `builder` / `reviewer`，
搭配當前環境實際可用的子 agent 工具。

Codex 額外注意：
- 角色或子 agent 工具不可用時**如實說明**，不冒充已委派或通過獨立驗證。
- 平行派工只用在寫入範圍不重疊的工作；提醒對方有其他人同時在改檔案，不得還原他人的修改。
- 不自動 commit、push 或部署。
