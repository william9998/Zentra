# ZEN-008 Run trigger 契約裁決交接

**任務**：`ZEN-008` OCI image 型 Agent — Run ingress trigger  
**從**：@architect　**給**：@qa-verifier  
**日期**：2026-09-22  
**Spec 版本**：`v0.3.1`

## 1. 裁決

QA 指出的契約缺口成立。Console 和外部 REST 都呼叫同一個
`POST /v1/agents/{agent_id}/runs`；目前又沒有 session/API-key 認證，伺服器不能可靠判斷 HTTP caller
是不是 Console。

最終裁決：`trigger` 表示**平台接入協定**，不是 caller UI 身分。

- REST router（包含 Console 呼叫）固定寫 `API`。
- FastMCP `tools/call` handler 固定寫 `MCP`。
- `CONSOLE` 自枚舉移除。
- request body 出現 `trigger` 是未知欄位，回 `422 VALIDATION_ERROR` 且不建 Run。
- `X-Zentra-Trigger`、`Origin`、`Referer` 等 header 都不能改寫 trigger。

## 2. 為什麼不選其他方案

| 方案 | 裁決 |
|---|---|
| body `trigger=CONSOLE` | 可由任何 client 偽造，且新增 caller 未授權欄位；否決 |
| `X-Zentra-Trigger` / Origin / Referer | header 可偽造，Origin/Referer 也可能缺失；否決 |
| 另開 Console-only endpoint | 仍無可信身分，還會破壞 Console 同源直接消費 `/v1` 的單一契約；否決 |
| 從 session cookie/API key 類型推導 | 未來有認證後可另設 server-derived caller 欄位；目前 MVP 不具備前提 |

## 3. 已更新檔案

| 檔案 | 變更 |
|---|---|
| `specs/SPEC.md` | bump `v0.3.1`，明定 trigger 語意與移除 `CONSOLE` |
| `specs/api-contract.md` | bump `v0.3.1`，固定 REST/API、MCP/MCP，禁止 body/header discriminator |
| `specs/features/ZEN-008/spec.md` | Console/REST/MCP 流程與驗收前置同步 |
| `coordination/BOARD.md` | 新增 Q-008 resolved 與裁決摘要 |

## 4. QA 要更新的 acceptance

請由 @qa-verifier 修改 `specs/features/ZEN-008/acceptance.md`（architect 不越界代改）：

1. 文件依據改為 SPEC/API `v0.3.1`。
2. `Z8-26` 保持 REST `trigger=API`，另加 body `trigger` 回 422、spoof header 不改值。
3. `Z8-29` Console 成功後 DB 應為 `trigger=API`，並斷言前端不送 discriminator。
4. `Z8-30` 三筆的 trigger 改為 REST=`API`、Console=`API`、MCP=`MCP`；仍須證明三門面走同一 `q.run`。
5. 全文不得再把 `CONSOLE` 當合法 Run trigger。

更新後請回寫 `qa-verifier-to-architect-trigger-resolution.md`。這次變更不要求 UI designer 修改可見流程；
Console 仍呼叫同一個 Run endpoint。

## 5. 驗證狀態

| 檢查 | 結果 | 證據 |
|---|---|---|
| 規格一致性 | 待 QA 回交後複核 | SPEC/API/feature 已同步到 v0.3.1 |
| 產品測試 | 未跑 | 尚未實作 |

## 6. 未決問題

- 無。Q-008 已在 BOARD 標為 `RESOLVED`。
