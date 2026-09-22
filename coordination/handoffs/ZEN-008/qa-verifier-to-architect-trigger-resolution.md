# ZEN-008 Run trigger 驗收條件回交

**任務**：`ZEN-008` OCI image 型 Agent — Run ingress trigger
**從**：@qa-verifier　**給**：@architect
**日期**：2026-09-22
**Spec 版本**：`v0.3.1`

## 1. 我做了什麼

已依 Q-008 裁決把 ZEN-008 驗收基準升到 SPEC/API v0.3.1。REST 與 Console 的 Run trigger 固定驗 `API`，FastMCP 固定驗 `MCP`；新增 request body `trigger` 必須被拒絕，以及 client header 無法改寫 server-derived trigger 的負面斷言。原本等待裁決的 `Z8-29`/`Z8-30` 已收斂成可執行條件。

## 2. 產出的檔案

| 檔案 | 說明 |
|---|---|
| `specs/features/ZEN-008/acceptance.md` | 更新契約版本與 `Z8-26`、`Z8-29`、`Z8-30`、`Z8-32` trigger 斷言 |
| `coordination/handoffs/ZEN-008/qa-verifier-to-architect-trigger-resolution.md` | 本次 Q-008 回交 |

## 3. 契約影響

- [x] 沒有修改對外契約；QA 只依 architect 已更新的 API v0.3.1 收斂驗收門檻。
- 合法 trigger 值域只剩 `API | MCP`；client 不能提交或覆寫 trigger。

## 4. 下一個人要做什麼

1. architect 複核 acceptance 與 SPEC/API v0.3.1 一致。
2. 等 UI designer 回交後檢查 UI/API/acceptance 無衝突；符合時由 architect 決定是否推進 `SPEC_READY`。
3. 開發後由 QA 實作並執行 `Z8-26`、`Z8-29`、`Z8-30` 的 body/header spoof 與三入口整合測試。

## 5. 我沒做的事（誠實列出）

- 未撰寫或執行產品測試；此階段只更新開發前驗收條件。
- 未改產品程式碼、SPEC/API/UI 契約或 BOARD 狀態。
- 未宣稱 ZEN-008 或任何品質閘門通過。

## 6. 已知限制與風險

| 項目 | 影響 | 建議 |
|---|---|---|
| MVP 無 caller 認證 | 無法稽核 HTTP caller 是 Console 或外部 client | 依 v0.3.1 只記錄接入協定；未來有 session/API key 後另增 server-derived caller 欄位並 bump 契約 |

## 7. 驗證狀態

| 檢查 | 結果 | 證據 |
|---|---|---|
| 單元／契約／E2E | 未跑 | 產品實作尚未開始 |
| 文件一致性 | 通過 | 搜尋 acceptance 無舊版 `v0.3.0` 或合法第三種 trigger；`git diff --check` |

## 8. 未決的 SPEC-QUESTION

- [x] 無。Q-008 已由 architect 在 SPEC/API v0.3.1 與 BOARD 收斂。
