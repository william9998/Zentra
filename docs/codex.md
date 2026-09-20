# 在 Zentra 使用 Codex

Zentra 已有 Codex 專案入口與七個專業角色。Claude Code 與 Codex 共用
`specs/SPEC.md`、契約、BOARD 與交接單，不需建立另一份產品規格。

## 各種檔案的用途

| 路徑 | 用途 | 何時讀取 |
|---|---|---|
| `AGENTS.md` | 共用協作規則、必讀文件與寫入邊界 | Codex 啟動時載入專案指令 |
| `CLAUDE.md` | Claude Code 專用入口 | 不依賴 Codex 自動載入它 |
| `.claude/agents/<role>.md` | 角色職責說明 | 本專案要求兩種工具的對應角色都讀 |
| `.codex/agents/<role>.toml` | Codex 子 agent 名稱、說明與指示 | Codex 發現角色，派工時使用 |
| `.agents/skills/<name>/SKILL.md` | 可重複使用的工作流程 | 選中技能時讀完整內容 |
| `specs/` | 產品的唯一規格與驗收條件 | 每個 agent 按 AGENTS.md 主動開檔讀取 |
| `coordination/` | 任務狀態、交接、漂移證據 | 開工、討論裁決與交棒時 |

`AGENTS.md` 不會把所有連結文件自動展開；因此派工時必須明列 SPEC、feature spec、
角色文件、契約與交接單。資料夾中有檔案不等於 agent 已讀過。
不需要額外的 `CODEX.md`。本專案也沒有必須新增的 `.codex/config.toml`：七個角色使用
獨立 TOML，模型與權限設定沿用父任務。需要調整全專案參數時再增加 config。

## 七個角色

| 名稱 | 責任 |
|---|---|
| `architect` | 定規格、契約、ADR，裁決 SPEC-QUESTION |
| `ui-designer` | 設計流程、元件與狀態 |
| `frontend-dev` | 原生 HTML/CSS/JS Console |
| `backend-dev` | API、Celery、資料模型、SDK |
| `platform-eng` | Docker、沙盒、compose、發布流程 |
| `qa-verifier` | 開發前驗收條件、開發後測試與 DONE 裁定 |
| `spec-keeper` | 稽核 P1–P5 與契約漂移 |

角色檔規定的是工作責任，並不是作業系統強制的目錄 ACL。仍須靠派工範圍、diff 審查、
測試與獨立稽核落實；不能保證有了 Markdown 就永遠不會偏離規格。

## 如何使用

在 Codex 開啟這個專案。修改入口或角色設定後，以新任務／重新啟動的 session 驗證載入；
skills 通常會自動重新發現，未顯示時重啟客戶端。
先做不改程式碼的檢查：

```text
$zentra-align ZEN-000
```

也可以直接說「讀 AGENTS.md 與 SPEC，回報七個可用角色、目前任務與規格矛盾」。
要開始一個功能時：

```text
$zentra-task 接續 ZEN-000。先確認 feature spec 和驗收條件，
依 AGENTS.md 派遣所需角色；可獨立的部分平行，完成後由 QA 與 spec-keeper 驗證。
```

| Skill | 用途 | 範例 |
|---|---|---|
| `zentra-align` | 唯讀對齊規格與現況 | `$zentra-align ZEN-000` |
| `zentra-task` | 按依賴派工並接續交棒 | `$zentra-task 實作 ZEN-000` |
| `zentra-handoff` | 依模板產生交接單 | `$zentra-handoff ZEN-000 backend-dev qa-verifier` |
| `zentra-verify` | QA 驗收與品質閘門 | `$zentra-verify ZEN-000` |
| `zentra-drift` | 稽核指定範圍的規格漂移 | `$zentra-drift apps/api` |

這些 `$` 名稱是 Codex skills；不是把 Claude 的 `/task` 等指令原封不動複製過來。
功能尚無規格或驗收條件時，派工流程會先補文件再實作。

## agent 如何討論

主 agent 協調子 agent；有訊息工具時可交換問題與證據，沒有時由主 agent 轉交。
產品契約的變更都交給 architect 裁決。結論寫回 ADR／契約，交接寫入
`coordination/handoffs/<task-id>/`，後續角色才能使用。

平行工作者共用檔案時不得同改同一區域或還原別人的修改。BOARD 更新由主 agent 串行整合，
QA 保有 DONE 的裁定權。七個角色按需啟動，不代表七個常駐服務，也不會自動一直互相聊天。

## 維護與驗證

- 修改角色時，同步 `.claude/agents/<role>.md` 正文與 `.codex/agents/<role>.toml` 的
  `developer_instructions`；檢查 `name`、`description` 與 TOML 語法。
- SPEC 與契約改版時檢查角色和 skills 是否殘留旧流程；優先引用權威文件，避免重抄數值或狀態表。
- 把 `.codex/agents/` 與 `.agents/skills/` 一起加入版本控制，其他人 checkout 才能得到相同設定。
- 若新 session 看不到角色，檢查專案路徑、客戶端版本與專案信任／管理設定；不要直接放寬權限。
- TOML／SKILL 語法檢查不等於完整功能驗收。實際派工後還要確認角色有讀檔、遵守範圍並留下交接。

文件依據（2026-09-20 查閱）：
[AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)、
[Custom agents](https://learn.chatgpt.com/docs/agent-configuration/subagents)、
[Skills](https://learn.chatgpt.com/docs/build-skills)。
