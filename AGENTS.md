# Zentra — 協作規則

> 這份是 **Claude Code 與 Codex 共用**的一份協作規則，刻意保持短。
> 「我們在做什麼」看 [`specs/SPEC.md`](./specs/SPEC.md)；「現在該做什麼」看
> [`coordination/BOARD.md`](./coordination/BOARD.md)。
> 對齊 SPEC `v0.2.2`：單機 `docker compose`（postgres · redis · registry · api · worker）、
> L0–L3 四層、原生 HTML/CSS/JS 前端。背景是 [ADR-0005](./specs/adr/ADR-0005-mvp-single-host-scope.md)
> 與 [ADR-0006](./specs/adr/ADR-0006-vanilla-frontend.md)。

## 五條不可妥協的立場（違反就是 bug）

| | 立場 |
|---|---|
| **P1** | 使用者程式碼**永遠不在平台行程內執行**。`exec` 一律在沙盒。 |
| **P2** | Agent 框架**統一 LangChain + LangGraph**。 |
| **P3** | 每個 Agent **天生就有 API + MCP 兩個門面**，由平台從同一份 `entrypoints` 自動生成。 |
| **P4** | **沙盒內拿不到任何長期憑證**。沙盒的唯一對端是 Internal Gateway（`app` 的 `/internal/*`）。 |
| **P5** | **Agent 版本不可變**。發布後要改就發新版。 |

全文在 `specs/SPEC.md` §2。

## 三個角色

| 角色 | 做什麼 | 可寫 |
|---|---|---|
| [`architect`](.claude/agents/architect.md) | 規格、契約、ADR | `specs/**`、`docs/architecture/**`、`docs/design/**` |
| [`builder`](.claude/agents/builder.md) | 全部產品程式碼（後端 + Console + 沙盒/平台） | `apps/**`、`packages/**`、`docker/**`、`compose.yaml` |
| [`reviewer`](.claude/agents/reviewer.md) | 驗收條件、測試、規格漂移稽核 | `tests/**`、`specs/features/*/acceptance.md`、`coordination/DRIFT.md` |

角色定義只有一份（`.claude/agents/*.md`）；`.codex/agents/*.toml` 指向它，不另外維護內容。

## 流程分級（不要每件事都走全套）

| 改動規模 | 怎麼做 |
|---|---|
| **小** — 單一區域、不動契約、不碰沙盒/憑證（修 bug、調文案、加一個欄位的顯示） | **主 session 直接做**。不叫 subagent、不寫交接單。跑受影響的那幾個測試就好。 |
| **中** — 跨區域（後端 + Console + 沙盒），但契約不變 | 派**一個** `builder` 一次做完，自己跑測試後回報。要不要再叫 `reviewer` 看風險決定。 |
| **大** — 會改 `api-contract.md` / `ui-contract.md`、需要新 ADR、或動到沙盒與憑證邊界 | `architect` 先定契約 → `builder` 實作 → `reviewer` 驗。**只有這一級寫交接單。** |

判斷不了是哪一級 → 當成小的先做，做到發現契約要改再升級。

## 規格不清楚怎麼辦

**直接問使用者。** 不要猜，也不要在程式碼寫 `// TODO: 待確認`。
使用者不在、而你能先做不相依的部分 → 先做，把問題寫在回報裡。
只有跨多個 session 才卡住的問題，才記到 `coordination/BOARD.md`。

## 契約優先

前端與後端不直接溝通，兩邊各自對齊 `specs/api-contract.md`。

- 契約沒定之前，**前端不准猜欄位名，後端不准自創回應格式**。
- 要改契約 → 先讓 `architect` 改 `api-contract.md` 並 bump 版本，再改實作與測試。
- Run 狀態字串、錯誤 `code` 一律引用 `SPEC.md` §9 / `api-contract.md` §0，不得自創。
  `WAITING_INPUT` 在 v0.2.0 已移除。

## 進 DONE 之前

1. 沒有違反 P1–P5
2. 實作與 `api-contract.md` 一致，受影響的測試綠燈（**沒過就說沒過，附輸出**）
3. 動到沙盒 / 憑證 / 網路出口 → 隔離驗證跑過，`capabilities()` 回報誠實

## 共同鐵則

1. **SPEC 優先**：程式碼與 SPEC 衝突，先改 SPEC 或先問，不准「順手」改行為。
2. **誠實回報**：測試沒過就說沒過，沒做的明講，不要用「已完成」帶過。
3. **不擴張範圍**：發現順帶該修的 → 記在 BOARD 或回報裡，不要自己加戲。
   特別是 BOARD §刻意不做 那張表 —— 那是降維的結果，不是漏掉的待辦。
4. **沿用既有慣例**：寫程式碼前先讀鄰近的檔案，命名、結構、錯誤處理跟著走。
5. **安全不妥協**：任何讓使用者程式碼跑在沙盒外、讓長期憑證進沙盒、或讓沙盒直連資料層的設計，一律拒絕。

## 派工時要寫清楚的事

派 subagent（中／大級）時，prompt 裡一定要有：任務一句話、可寫範圍、**明確的「不做」清單**、
這次會用到的端點與實測過的 payload 形狀、已知的坑。
派工者多寫 500 token，收工的人少讀 10K —— 不要讓 subagent 自己去挖。

平行派工只用在寫入範圍**不重疊**的工作，且要提醒對方有人同時在改檔案，不得還原他人的修改。

## Codex 專用

- 根目錄這份 `AGENTS.md` 就是 Codex 的專案指令入口；細節見 [`docs/codex.md`](docs/codex.md)。
- `.agents/skills/` 提供 `zentra-task`、`zentra-verify`、`zentra-drift`、`zentra-handoff`。
- 若當前客戶端沒有子 agent 工具，明講；不得假稱已有獨立 agent 審核過。
