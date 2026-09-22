# ZEN-008 架構師交接給 QA 驗證

**任務**：`ZEN-008` OCI image 型 Agent 註冊、呼叫與一次性執行  
**從**：@architect　**給**：@qa-verifier  
**日期**：2026-09-22  
**Spec 版本**：`v0.3.0`

## 1. 我做了什麼

已定義從 OCI digest 註冊到 Console/REST/MCP 呼叫的完整契約。Image 本體在既有 Registry，
PostgreSQL 只存 metadata；`q.build` pull/inspect/validate，Run 沿用 `q.run`、一 Run 一盒、Gateway 回寫、
worker 唯一終態與銷毀。Feature spec §9 已列出開發前必須轉成可執行斷言的驗收範圍。

## 2. 產出的檔案

| 檔案 | 說明 |
|---|---|
| `specs/features/ZEN-008/spec.md` | 權威 feature spec；§9 是 acceptance 最低範圍 |
| `specs/adr/ADR-0008-oci-image-agent-packaging.md` | OCI Registry、digest、取代 bundle 的決策 |
| `specs/SPEC.md` | v0.3.0 主規格與 P1–P5 落點 |
| `specs/api-contract.md` | v0.3.0 公開 API、MCP 與 validation endpoint 契約 |
| `docs/architecture/oci-image-agent-flow.md` | 註冊與 Run 資料流圖 |

## 3. 契約影響

- [x] 有影響：API contract 已 bump 到 `v0.3.0`。
- `POST /v1/agents/{id}/versions` 是 JSON `semver + canonical image_ref`，不再是 multipart bundle。
- 錯誤枚舉新增 `VERSION_CONFLICT`；版本失敗使用 `BUILD_FAILED + details.stage`。
- Runs/SSE/MCP 的外部 shape 不變；底層 SandboxSpec 改用發布的 exact digest。

## 4. 下一個人要做什麼

先讀本交接、AGENTS.md、SPEC v0.3.0、自己的角色檔、ADR-0008、ZEN-008 spec、API contract v0.3.0。

1. 在任何開發開始前建立 `specs/features/ZEN-008/acceptance.md`。
2. 把 feature spec §9 每一項轉成可執行斷言，明確分單元、契約、Docker 整合、E2E 與安全測試。
3. 至少涵蓋 digest 不可變、DB 無 blob/secret、q.build 冪等、validation token 邊界、runtime contract、P1/P4、RepoDigest 精確比對、一 Run 一盒、REST/MCP schema 等價、Console 主線與 ZEN-000/004 隔離回歸。
4. gVisor/不可信第三方 image 仍應標 skipped/未涵蓋，不得把 digest 當供應鏈安全證明。
5. 完成後寫 `coordination/handoffs/ZEN-008/qa-verifier-to-architect.md`；由 architect 檢查無衝突後才能推 `SPEC_READY`。

## 5. 我沒做的事

- 未建立 `acceptance.md`，避免 architect 冒充 QA 定義通過門檻。
- 未執行測試，因為產品實作尚未開始。
- 未推進 `SPEC_READY`；UI contract 與 acceptance 尚未完成。

## 6. 已知限制與風險

| 項目 | 影響 | 建議 |
|---|---|---|
| 測試需要 OCI Registry | CI/本機可能沒有外部服務 | 建立測試用本地 registry fixture，但它不是產品 compose 服務；測試後清理 |
| digest cache 可能遮蔽 pull 測試 | 無法證明錯 digest/Registry failure | 測試前用唯一 repository/digest，分 cache hit/miss 兩組，勿用 tag |
| Docker 安全測試需真 daemon | 純 mock 不能證明隔離與銷毀 | 真 Docker 測試與 fake driver 單元測試分列，缺環境時標 skipped |

## 7. 驗證狀態

| 檢查 | 結果 | 證據 |
|---|---|---|
| 單元測試 | 未跑 | 尚無實作 |
| 契約測試 | 未跑 | 等 acceptance 與實作 |
| 建置 / 型別檢查 | 未跑 | 本次只有 Markdown 規格 |

## 8. 未決的 SPEC-QUESTION

- [x] 無。若可執行測試暴露契約不可驗證，先開 SPEC-QUESTION，不替開發者放寬門檻。
