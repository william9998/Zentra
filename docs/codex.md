# 在 Zentra 使用 Codex

Claude Code 與 Codex 共用 `specs/`、契約、BOARD 與交接單，不需要第二份產品規格。
協作規則也只有一份：根目錄的 [`AGENTS.md`](../AGENTS.md)。

## 各種檔案的用途

| 路徑 | 用途 | 誰維護內容 |
|---|---|---|
| `AGENTS.md` | 兩套工具共用的協作規則（P1–P5、三個角色、流程分級） | 這裡就是正本 |
| `CLAUDE.md` | Claude Code 專用入口（目錄地圖、常用指令、Claude 專屬提醒） | Claude Code 側 |
| `.claude/agents/<role>.md` | **角色定義的正本**，兩套工具共用 | 這裡就是正本 |
| `.codex/agents/<role>.toml` | 讓 Codex 發現角色；內容只是指向上面那份 | 不放實際內容 |
| `.claude/commands/<name>.md` | **流程的正本**（派工、驗收、漂移稽核、交接單） | 這裡就是正本 |
| `.agents/skills/zentra-<name>/SKILL.md` | 讓 Codex 發現流程；內容只是指向上面那份 | 不放實際內容 |
| `specs/` | 產品的唯一規格與驗收條件 | architect |
| `coordination/` | 任務狀態、交接、漂移證據 | 全隊 |

**正本只有一份。** 改角色或流程時只改 `.claude/` 底下那份，`.codex/` 與 `.agents/` 的指向檔不用動。
會需要改到指向檔的情況只有兩種：新增角色／流程，或改 `description`（Codex 靠它決定何時用）。

`AGENTS.md` 不會自動展開所有連結，所以派工時要明列該讀的檔案。
資料夾裡有檔案不等於 agent 已讀過。

## 三個角色

| 名稱 | 責任 |
|---|---|
| `architect` | 規格、契約、ADR。只在要改對外契約或做跨模組取捨時叫。 |
| `builder` | 全部產品程式碼：後端、Console、沙盒與平台。 |
| `reviewer` | 驗收條件、測試、P1–P5 與契約漂移稽核。 |

角色檔規定的是工作責任，不是作業系統強制的 ACL。還是要靠派工範圍、diff 審查與測試落實。

## 如何使用

在 Codex 開啟這個專案。改完入口或角色設定後，用新 session 驗證載入；
skills 通常會自動重新發現，沒出現就重啟客戶端。

小改動直接說要改什麼就好，不需要走 skill。要走流程時：

| Skill | 用途 | 範例 |
|---|---|---|
| `zentra-task` | 判斷該自己做還是派工，並派給對的角色 | `$zentra-task 實作 ZEN-009` |
| `zentra-verify` | 跑受影響的測試與隔離驗證 | `$zentra-verify ZEN-009` |
| `zentra-drift` | 稽核指定範圍的規格漂移 | `$zentra-drift apps/api` |
| `zentra-handoff` | 依模板產生交接單（只有大功能需要） | `$zentra-handoff ZEN-009 builder reviewer` |

這些 `$` 名稱是 Codex skills，與 Claude 的 `/task` 等指令是同一份流程的兩個入口。

## 邊界

- 規格不清楚 → **直接問使用者**，不要自己決定，也不要寫 `// TODO: 待確認`。
- 產品契約的變更交給 `architect`，結論寫回 ADR／契約。
- 平行工作者不得同改同一區域或還原別人的修改。BOARD 更新由主 agent 串行整合。
- 若當前客戶端沒有子 agent 工具，明講；不得假稱已有獨立 agent 審核過。
- 角色按需啟動，不是三個常駐服務，也不會自動一直互相聊天。

## 維護

- 改角色或流程 → 只改 `.claude/agents/` 或 `.claude/commands/` 的正本。
- SPEC 與契約改版時檢查角色檔有沒有殘留舊流程；優先引用權威文件，避免重抄數值或狀態表。
- `.codex/agents/` 與 `.agents/skills/` 要進版本控制，其他人 checkout 才有相同設定。
- 新 session 看不到角色 → 檢查專案路徑、客戶端版本與專案信任設定，不要直接放寬權限。
- TOML／SKILL 語法檢查不等於功能驗收。實際派工後還要確認角色有讀檔、遵守範圍。

文件依據（2026-09-20 查閱）：
[AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)、
[Custom agents](https://learn.chatgpt.com/docs/agent-configuration/subagents)、
[Skills](https://learn.chatgpt.com/docs/build-skills)。
