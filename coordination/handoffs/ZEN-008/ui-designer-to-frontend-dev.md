# ZEN-008 UI 設計師交接給前端開發

**任務**：`ZEN-008` OCI image 型 Agent 註冊、呼叫與一次性執行  
**從**：@ui-designer　**給**：@frontend-dev（須先經 @architect 裁定 `SPEC_READY`）  
**日期**：2026-09-22  
**Spec 版本**：`v0.3.1`

## 1. 我做了什麼

UI contract 已升至 v0.5.1，將舊 `.tar.gz` upload 流程完整替換為 canonical OCI digest 註冊，並同步
Run trigger 裁決：Console 不送 discriminator，server 將同源 REST Run 記為 `API`。
既有五頁 IA 不變：Agent 建立放在列表頁 dialog；版本註冊沿用 version-new；版本選擇、entrypoint 與 JSON input
放在 Agent 詳情；Run 建立後導向既有 Run 觀測頁。新增元件均定義 Loading / Empty / Error /
Partial or Streaming / Success 五態，且逐項對齊 API contract v0.3.1。

## 2. 產出的檔案

| 檔案 | 說明 |
|---|---|
| `specs/ui-contract.md` | v0.5.1；五頁 IA、版本狀態、Agent 建立、OCI 註冊、Agent 列表/版本列表、Run composer、trigger 語意與五態 |
| `docs/design/oci-image-agent-console.md` | 端到端操作流程、文字 wireframe、API 欄位對照、錯誤文案與無障礙要求 |
| `coordination/handoffs/ZEN-008/ui-designer-to-frontend-dev.md` | 本交接單 |

## 3. 契約影響

- [x] 有影響：`specs/ui-contract.md` 已更新為 `v0.5.1`，對齊 `api-contract.md v0.3.1`。
- `/console/agents` 新增頁內 Agent metadata dialog，呼叫 `POST /v1/agents`。
- `/console/agents/{id}/versions/new` 改送 JSON `semver + image_ref`，不再有檔案 input 或 multipart。
- Agent 詳情新增 PUBLISHED version / entrypoint 選擇與 JSON input，送既有 async Run endpoint。
- Console Run body 只送 `version/entrypoint/input`，不送 trigger 欄位或 header；server 記錄 `trigger=API`。
- 沒有新增 Console route、API 欄位、狀態字串、錯誤碼或設計 token。

## 4. 下一個人要做什麼

1. 等 @architect 將 ZEN-008 推進 `SPEC_READY`，再依 UI contract 實作；不得提前視為可開工。
2. 將 `version-upload.js` 替換成 `version-registration.js`，移除 `.tar.gz`、FormData 與拖放 UI。
3. 在 `api.js` 實作 Agent / Version / create Run wrappers 與 view-model normalization。
4. 實作 `create-agent-dialog.js`、`agent-list.js`、`agent-version-list.js`、`run-composer.js` 與各五態。
5. 對 PUBLISHED、BUILDING、FAILED 與 API error 路徑做 Playwright 驗證；所有 interval 在終態與 destroy 時清除。

## 5. 我沒做的事

- 未修改 `apps/console/**` 或任何產品程式碼。
- 未新增即時 build stage；API 只有 aggregate `BUILDING`，精確 stage 只在 FAILED 的 `error.details.stage`。
- 未替 architect 推進 `SPEC_READY`，也未執行 QA 驗收。
- 未設計 image tar upload、Registry credential、Docker build 或自訂 container runtime 控制項。

## 6. 已知限制與風險

| 項目 | 影響 | 建議 |
|---|---|---|
| API 不回即時 build stage | BUILDING 只能顯示 pull/inspect/validate 聚合進度 | 不猜 stage；只在 FAILED 顯示 `details.stage` |
| canonical image ref 很長 | 列表與窄螢幕可能溢位 | 視覺可短顯示或換行；Copy 必須是完整值 |
| 動態 JSON Schema | 原生 JS 若生成完整表單會超出 MVP 複雜度 | 使用 JSON textarea + schema `<details>`；後端做權威驗證 |
| semver 不可重用 | FAILED 後不能用同 semver 重送 | 顯示完整失敗證據，導引使用者以新 semver 註冊 |

## 7. 驗證狀態

| 檢查 | 結果 | 證據 |
|---|---|---|
| 單元測試 | 未跑 | 本次只有設計文件 |
| API 欄位人工對照 | 已完成 | `docs/design/oci-image-agent-console.md` §5 |
| Trigger 契約同步 | 已完成 | Console request 無 discriminator；REST=`API`、MCP=`MCP` |
| 五態檢查 | 已完成 | `specs/ui-contract.md` §5.1–§5.10 |
| 格式檢查 | 已通過 | `git diff --check` 無輸出 |

## 8. 未決的 SPEC-QUESTION

- [x] 無。BUILDING 即時 stage 缺少 API 欄位已在 UI 契約中明示為聚合進度，不要求前端猜測或新增欄位。
