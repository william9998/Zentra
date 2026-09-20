# 交接單 Handoff

**任務**：`DEV-CODEX` 專案 Codex 協作設定與角色對齊
**從**：@architect　**給**：@orchestrator
**日期**：2026-09-20
**Spec 版本**：`v0.2.1`

## 1. 我做了什麼

唯讀檢查七份 `.codex/agents/*.toml` 與七份 `.claude/agents/*.md`，確認 TOML 語法、必要欄位及角色正文等價性。七份 TOML 均可解析，均含 `name`、`description`、`developer_instructions`；審核當時七份 instructions 均與 Claude 對應角色正文完全相同。各角色均有可寫範圍，主要必讀資料齊備；共通 feature spec 與交接單要求由根目錄 `AGENTS.md` 補足。

發現的內容漂移來自原角色文件，以下裁決只把角色指引對齊既有 SPEC、ADR 與契約，不新增產品行為，不 bump 產品 SPEC。主 session 負責同步修改兩套角色定義與補充 Codex 使用方式；本次只產出此交接單。

## 2. 產出的檔案

| 檔案 | 說明 |
|---|---|
| `coordination/handoffs/DEV-CODEX/architect-to-orchestrator.md` | 角色對齊裁決、驗證證據及主 session 待辦 |

## 3. 契約影響

- [x] 沒有影響對外契約
- [ ] 有影響 → 列出變更，並同步更新契約

這是開發工具設定；`.agents/skills` 是 Codex 開發流程技能，不是 SPEC §11 排除的 Zentra 產品 Skill Registry。

## 4. 下一個人要做什麼

將以下修正同步套用於 `.claude/agents/<role>.md` 與 `.codex/agents/<role>.toml`。下列位置以審核前檔案內容定位，修改後行號會移動。

| 角色與原內容 | 裁決 | 既有依據 |
|---|---|---|
| `platform-eng`：五個常駐服務含 `console`；要加第六個才提問 | 改為四個服務 `postgres / redis / api / worker`，新增第五個需提問；網路與 socket 規則刪除已取消的 console 服務 | SPEC §6.1、ADR-0006、詳細架構 §9 |
| `platform-eng`：範例 `wait_ready` 且缺 `logs` | 移除重複介面範例，引用 ADR-0004 頂端的現行方法清單與當前 feature spec；不要自行補參數契約 | ADR-0004 頂端註記：`create / wait / logs / destroy / capabilities` |
| `backend-dev`：worker「申請沙盒 → 轉送事件 → 回寫狀態」 | 刪除 worker 轉送事件的職責，註明事件由沙盒 POST 到 Internal Gateway | SPEC §7.2、詳細架構 §3 |
| `spec-keeper`：前端型別/codegen 稽核 | 改檢查原生 JS 的 `api.js`、同源 API 消費與資料欄位是否符合契約，不要求 TypeScript 或 codegen | SPEC §6、ADR-0006、UI 契約 §6 |
| `spec-keeper`：必讀未列 `AGENTS.md` | 補列根協議；角色指引也明示遵守共通必讀與交接規則 | AGENTS §2、§4 |
| `qa-verifier`：品質評測走 `q.eval` | 移除強制使用 `q.eval` 的指令，既有正常/失敗/邊界/契約/安全驗證保留 | 詳細架構 §5 明確移除 `q.eval`；SPEC §11 |
| `qa-verifier`：對齊固定 `api-contract.md v0.2.0` | 改引用當前 `api-contract.md` 版本，驗證時回報實際版本 | API 契約頁首目前為 v0.2.1 |
| `ui-designer`：Run 可跑幾小時、七種 Run 狀態 | 改為最長 15 分鐘，Run 狀態直接引用 SPEC §9 與 UI 契約 §2 | SPEC §9 六種狀態、§10 15 分鐘上限 |
| `backend-dev / qa-verifier / spec-keeper`：SPEC §8.4 當錯誤碼枚舉 | 格式仍可引用 SPEC §8.4，枚舉必須引用 API 契約 §0 | SPEC §8.4 是格式範例；API 契約 §0 是完整錯誤碼表 |

共通協議補充時，明確區分角色產品寫入範圍、自己負責的 BOARD 區段與交接單；讓 `spec-keeper` 的唯讀產品稽核角色仍可留下自己的稽核交接記錄。修改完成後重新執行七份 TOML 解析與 Markdown 正文等價檢查。

## 5. 我沒做的事（誠實列出）

- 未修改任何角色文件、產品 SPEC、產品程式碼或測試。
- 未替主 session 審核尚在建立中的 skills 與使用文件。
- 未測試重開 Codex 後的角色/skill 載入；靜態解析通過不等於實際載入驗證。
- 未執行功能測試、沙盒測試或產品安全放行。

## 6. 已知限制與風險

| 項目 | 影響 | 建議 |
|---|---|---|
| 角色正文在 Claude / Codex 各保存一份 | 單邊修改會造成工作方式分歧 | 每次修正同步兩份，重做等價檢查 |
| 指令中的可寫範圍屬協作規則 | 不能代替執行環境的檔案權限隔離 | 不向使用者宣稱角色指令提供硬性 sandbox |
| 舊 ADR 本文保留歷史設計 | 只讀舊範例可能恢復被移除的 MVP 行為 | 先讀頂端取代註記，依 SPEC v0.2.1 與最新 feature 判斷 |

## 7. 驗證狀態

| 檢查 | 結果 | 證據 |
|---|---|---|
| TOML 語法 | 通過，7/7 | Python `tomllib.loads()` 解析 `.codex/agents/*.toml` |
| 必要欄位 | 通過，7/7 | 每份皆有 `name / description / developer_instructions` |
| 角色正文等價 | 審核當時通過，7/7 | `developer_instructions.strip()` 等於對應 Markdown 移除 YAML frontmatter 後的 `.strip()` |
| 規格對齊 | 已提出修正裁決，待主 session 套用後確認 | 上方 §4 具體內容與依據 |
| 功能與安全測試 | 未跑，不適用於此唯讀角色文件審核 | 未改產品程式碼 |
| Codex 實際載入 | 未跑 | 未重開 session 或執行載入驗證 |

## 8. 未決的 SPEC-QUESTION

- [x] 本次角色對齊沒有需要新產品決策的 SPEC-QUESTION。
- `version_id` 的「SHA-256 前 12 碼」已有 API 契約 §2 明訂，不是本次需改動的歧義。
- 不對超出本次角色文件檢查範圍的產品契約完整性作結論。
