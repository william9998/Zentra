# ZEN-008 架構師交接給 UI 設計師

**任務**：`ZEN-008` OCI image 型 Agent 註冊、呼叫與一次性執行  
**從**：@architect　**給**：@ui-designer  
**日期**：2026-09-22  
**Spec 版本**：`v0.3.0`

## 1. 我做了什麼

已裁定 OCI image 取代尚未實作的 bundle/venv 發布路徑。使用者先 push 到既有 Registry，再把
canonical `repository@sha256:...` 註冊給 Zentra；平台 pull、隔離驗證後才發布。Console 不直接上傳 image tar，
而是建立 Agent metadata、註冊 digest、輪詢版本狀態，並從 Agent 詳情選版本/entrypoint 發起 Run。

## 2. 產出的檔案

| 檔案 | 說明 |
|---|---|
| `specs/adr/ADR-0008-oci-image-agent-packaging.md` | Registry、digest、單一路徑與 runtime contract 決策 |
| `specs/features/ZEN-008/spec.md` | 端到端流程、UI 必要行為、資料模型與角色順序 |
| `specs/SPEC.md` | 主規格 v0.3.0 |
| `specs/api-contract.md` | API contract v0.3.0 |

## 3. 契約影響

- [x] 有影響：`POST /versions` 改為 JSON `semver + image_ref`；版本回應新增 image artifact metadata。
- [ ] `specs/ui-contract.md` 尚未更新；這是本交接的工作，不得把 feature spec 當 UI contract。

## 4. 下一個人要做什麼

先讀本交接、AGENTS.md、SPEC v0.3.0、自己的角色檔、ADR-0008、ZEN-008 spec、API contract v0.3.0。

1. 更新 `specs/ui-contract.md` 版本與對齊 Spec。
2. 把 `/console/agents/{id}/versions/new` 從 `.tar.gz` 拖放改成 Docker image 註冊：semver、canonical digest reference、不可逆確認、輪詢 BUILDING/PUBLISHED/FAILED。
3. 定 Agent 建立入口與 Agent 詳情的呼叫區：選 PUBLISHED version、entrypoint、JSON input，建立 Run 後導向既有 Run 觀測頁。
4. 定義 image digest、runtime contract、BUILD_FAILED stage 的 Loading/Empty/Error/Streaming/Success 五態與無障礙文案。
5. 不新增第六個 Console route，不引框架，不設計 tar upload、Registry credential 表單或 Docker build UI。
6. 完成後寫 `coordination/handoffs/ZEN-008/ui-designer-to-architect.md`，讓 architect 檢查 UI/API 是否一致。

## 5. 我沒做的事

- 未修改 `specs/ui-contract.md`，遵守 UI 設計師唯一擁有權。
- 未做任何前端或後端實作。
- 未裁定 `SPEC_READY`；UI contract 與 QA acceptance 都完成後才可裁定。

## 6. 已知限制與風險

| 項目 | 影響 | 建議 |
|---|---|---|
| `image_ref` 很長 | 表格與窄螢幕易溢位 | 顯示 repository + digest 短碼，但 Copy 必須複製完整 canonical ref |
| BUILDING 包含 pull/inspect/validate | 可能比 bundle UI 原先預期久 | 顯示目前 stage；超過 3 秒依 ui-contract 顯示進度語意 |
| Registry credential 不屬 Console | 使用者可能以為可在表單貼密碼 | 文案明示由 VM 維運者預先配置，不提供 secret 輸入 |

## 7. 驗證狀態

| 檢查 | 結果 | 證據 |
|---|---|---|
| 單元測試 | 未跑 | 本次只有規格 |
| 契約測試 | 未跑 | 等 QA acceptance 與實作 |
| 規格一致性 | 已人工檢查 | ADR-0008、ZEN-008 spec、API v0.3.0 使用同一 digest 流程 |

## 8. 未決的 SPEC-QUESTION

- [x] 無。若 UI 契約發現 API 欄位不足，開新問題交 architect，不在前端自創欄位。
