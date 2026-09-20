# 任務看板 (Task Board)

> **唯一的任務真相。** 所有 agent 開工前看這裡，收工後更新這裡。
> 狀態機：`BACKLOG → SPEC_READY → IN_PROGRESS → REVIEW → QA → DONE`（任何階段可轉 `BLOCKED`）
> 規則：只有 `@architect` 能推進到 `SPEC_READY`；只有 `@qa-verifier` 能推進到 `DONE`。

## 進行中

| ID | 任務 | 狀態 | 負責 | 依賴 | 交接單 |
|---|---|---|---|---|---|
| ZEN-001 | 建立專案骨架與 monorepo 結構 | `BACKLOG` | @platform-eng | — | — |
| ZEN-002 | Agent Manifest schema 與 Registry 資料模型 | `BACKLOG` | @architect → @backend-dev | ZEN-001 | — |
| ZEN-003 | Sandbox 最小可行版（gVisor + 五層隔離 + 驗證測試） | `BACKLOG` | @platform-eng | ZEN-001 | — |
| ZEN-004 | Celery 派工骨架（佇列拓撲 + runner-worker + 冪等） | `BACKLOG` | @backend-dev | ZEN-002 | — |
| ZEN-005 | Zentra SDK v0（ctx.llm / tools / knowledge）+ Agent 程式碼契約 | `BACKLOG` | @architect → @backend-dev | ZEN-002 | — |
| ZEN-006 | Run API + SSE 事件串流 | `BACKLOG` | @backend-dev | ZEN-004 | — |
| ZEN-007 | Interface Generator（一份 entrypoints → REST + MCP schema） | `BACKLOG` | @backend-dev | ZEN-005 | — |
| ZEN-008 | Console：Run 觀測頁（串流 + 節點樹） | `BACKLOG` | @ui-designer → @frontend-dev | ZEN-006 | — |
| ZEN-009 | 知識庫 Ingestion + Hybrid Search | `BACKLOG` | @backend-dev | ZEN-001 | — |
| ZEN-010 | 沙盒安全驗證套件（逃逸 + 租戶隔離回歸測試） | `BACKLOG` | @qa-verifier | ZEN-003 | — |

## 被阻塞 (BLOCKED)

| ID | 阻塞原因 | 需要誰解 | 開始阻塞 |
|---|---|---|---|
| — | — | — | — |

## SPEC-QUESTION（規格提問）

> 規格不清楚時開在這裡，**不要自己決定**。由 `@architect` 裁決後收斂回 `specs/SPEC.md`。

| # | 提問 | 由 | 狀態 | 裁決 |
|---|---|---|---|---|
| Q-001 | 範例：Run 逾時後，已經產生的部分輸出要保留還是丟棄？ | @backend-dev | `OPEN` | — |

**提問格式**（新增時照抄）：
```
| Q-00X | <具體問題，附上為什麼卡住> | @<你> | OPEN | — |
```
提問後請同時把相關任務標為 `BLOCKED`，並繼續做不相依的部分。

## 已完成

| ID | 任務 | 完成日 | 驗證者 |
|---|---|---|---|
| — | — | — | — |
