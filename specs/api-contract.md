# API 契約 (API Contract)

| 欄位 | 值 |
|---|---|
| 契約版本 | `v0.1.0` |
| 擁有者 | `@architect` |
| 對齊 Spec | `specs/SPEC.md v0.1.0` |

> **這是前端與後端的唯一交會點。**
> 後端照此實作，前端照此 mock，QA 照此寫契約測試。
> **要改 → 開 `SPEC-QUESTION`，架構師裁決後 bump 版本，並同時通知三方。**
> 前端不准猜欄位名，後端不准自創回應格式。

---

## 0. 通則

- Base URL：`https://<host>/v1`
- 認證：`Authorization: Bearer <api_key>` 或 OIDC JWT
- 所有時間：RFC 3339 UTC（`2026-09-20T06:30:00Z`）
- 所有 ID：UUID v7（可排序）
- 分頁：cursor-based，`?limit=50&cursor=<opaque>`，回應含 `next_cursor`（無下一頁則 `null`）
- 冪等：寫入端點接受 `Idempotency-Key` header，24 小時內重送回相同結果

### 錯誤格式（全平台唯一）
```json
{
  "error": {
    "code": "SANDBOX_TIMEOUT",
    "message": "人類可讀的說明",
    "run_id": "0192...",
    "retryable": true,
    "details": {}
  }
}
```

### 錯誤 code 枚舉（不得自創，要加先改這份契約）
| code | HTTP | retryable | 說明 |
|---|---|---|---|
| `UNAUTHENTICATED` | 401 | false | token 無效或過期 |
| `PERMISSION_DENIED` | 403 | false | 已認證但無此 scope |
| `NOT_FOUND` | 404 | false | 資源不存在**或不屬於此租戶**（不洩漏存在性） |
| `VALIDATION_ERROR` | 422 | false | 請求格式錯誤，`details` 含欄位層級訊息 |
| `QUOTA_EXCEEDED` | 429 | true | 超出併發或用量配額，含 `Retry-After` |
| `AGENT_NOT_PUBLISHED` | 409 | false | Agent 版本未發布 |
| `SANDBOX_TIMEOUT` | — | true | 沙盒執行逾時（寫在 Run 的 error，非 HTTP） |
| `SANDBOX_ERROR` | — | true | 沙盒啟動或崩潰 |
| `TOOL_ERROR` | — | true | 工具呼叫失敗 |
| `EGRESS_BLOCKED` | — | false | 沙盒嘗試連非白名單位址 |
| `USER_CODE_ERROR` | — | false | 使用者 Agent 程式碼拋出例外 |
| `INTERNAL_ERROR` | 500 | true | 未預期錯誤 |

---

## 1. Runs（最重要的端點群）

### `POST /v1/agents/{agent_id}/runs` — 建立 Run（非同步）

Request：
```json
{
  "entrypoint": "default",
  "input": { "question": "..." },
  "version": "1.2.0",
  "config": {
    "timeout_seconds": 900,
    "tier": "short",
    "metadata": { "trace_id": "..." }
  }
}
```
| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| `entrypoint` | string | 否 | 預設 `"default"` |
| `input` | object | 是 | 由 Agent 的 entrypoint schema 驗證 |
| `version` | string | 否 | 省略則用 alias `prod` 指向的版本 |
| `config.timeout_seconds` | int | 否 | 預設 900，上限依 tier |
| `config.tier` | enum | 否 | `short` \| `long` \| `priority` |

Response `202`：
```json
{
  "run_id": "0192...",
  "agent_id": "0192...",
  "agent_version": "1.2.0",
  "status": "QUEUED",
  "created_at": "2026-09-20T06:30:00Z",
  "events_url": "/v1/runs/0192.../events"
}
```

### `POST /v1/agents/{agent_id}/runs:invoke` — 同步呼叫
同 Request。逾時上限 **60 秒**，超過回 `SANDBOX_TIMEOUT`。
Response `200`：與 `GET /v1/runs/{run_id}` 相同的完整 Run 物件。

### `GET /v1/runs/{run_id}`
```json
{
  "run_id": "0192...",
  "agent_id": "0192...",
  "agent_version": "1.2.0",
  "status": "SUCCEEDED",
  "trigger": "API",
  "input": {},
  "output": {},
  "error": null,
  "usage": {
    "input_tokens": 1520,
    "output_tokens": 340,
    "tool_calls": 3,
    "duration_ms": 8420,
    "cost_usd": "0.0182"
  },
  "created_at": "...", "started_at": "...", "finished_at": "..."
}
```
| 欄位 | 值域 |
|---|---|
| `status` | `QUEUED` \| `SCHEDULING` \| `RUNNING` \| `WAITING_INPUT` \| `SUCCEEDED` \| `FAILED` \| `CANCELLED` — **照抄 SPEC §9，不得自創** |
| `trigger` | `API` \| `MCP` \| `SCHEDULE` \| `FLOW` \| `CONSOLE` |
| `cost_usd` | **字串**（decimal，避免浮點誤差） |

### `GET /v1/runs/{run_id}/events` — SSE 串流
Header：`Accept: text/event-stream`，支援 `Last-Event-ID` 續傳。

```
id: 42
event: node.start
data: {"node":"llm_node","ts":"..."}

id: 43
event: token
data: {"node":"llm_node","delta":"這"}

id: 44
event: tool.call
data: {"tool":"web_search","args":{...},"call_id":"..."}

id: 45
event: tool.result
data: {"call_id":"...","status":"ok","result_preview":"..."}

id: 46
event: run.status
data: {"status":"SUCCEEDED"}
```
事件型別枚舉：`run.status`｜`node.start`｜`node.end`｜`token`｜`tool.call`｜`tool.result`｜`interrupt`｜`error`

> **前端必須處理**：重連（帶 `Last-Event-ID`）、重複事件去重（用 `id`）、離開頁面關閉連線。

### `POST /v1/runs/{run_id}/cancel` → `200`，status 轉 `CANCELLED`
### `POST /v1/runs/{run_id}/resume` — HITL 續跑
```json
{ "resume_value": { "approved": true } }
```
僅在 `status == "WAITING_INPUT"` 有效，否則 `409`。

### `GET /v1/runs` — 列表
Query：`agent_id`、`status`、`trigger`、`created_after`、`created_before`、`limit`、`cursor`
```json
{ "items": [ /* Run 精簡物件，不含 input/output */ ], "next_cursor": null }
```

---

## 2. Agents

| Method | Path | 說明 |
|---|---|---|
| `GET` | `/v1/agents` | 列表 |
| `POST` | `/v1/agents` | 建立（平台組裝路徑） |
| `GET` | `/v1/agents/{id}` | 詳情，含 endpoints |
| `PATCH` | `/v1/agents/{id}` | 改 metadata（**不可改已發布版本**，P5） |
| `DELETE` | `/v1/agents/{id}` | 軟刪除 |
| `GET` | `/v1/agents/{id}/versions` | 版本列表 |
| `POST` | `/v1/agents/{id}/versions` | 建立新版本（上傳路徑：回 upload URL） |
| `POST` | `/v1/agents/{id}/versions/{v}/publish` | 發布 |
| `PUT` | `/v1/agents/{id}/aliases/{alias}` | 切流量指向 |

Agent 詳情的 `endpoints`（P3 的體現）：
```json
{
  "endpoints": {
    "rest": "https://api.zentra.io/v1/agents/0192.../runs",
    "mcp": "https://api.zentra.io/mcp/agents/0192...",
    "openapi": "https://api.zentra.io/v1/agents/0192.../openapi.json"
  }
}
```

---

## 3. MCP 介面

端點：`https://<host>/mcp/agents/{agent_id}`（Streamable HTTP，MCP 標準協定）

| MCP 方法 | 對應行為 |
|---|---|
| `tools/list` | 回傳該 Agent 每個 entrypoint 一個 tool，schema 由 manifest 生成 |
| `tools/call` | 觸發一次 Run。預設同步等待（上限 60s）；`_async: true` 則回 `run_id` |
| `resources/list` | 列出該 Agent 綁定的知識集合 |
| `resources/read` | 讀取指定文件 |

> **P3 的實作要求**：`tools/list` 的 input schema 與 REST 的 `input` schema **必須由同一份 entrypoint 定義生成**。
> 契約測試要驗證兩者等價 —— 這是 QA 的必測項。

---

## 4. Tools / Knowledge（摘要）

| Method | Path | 說明 |
|---|---|---|
| `GET` `POST` | `/v1/tools` | Tool Registry 列表 / 註冊 |
| `POST` | `/v1/tools/{id}/test` | 測試呼叫（經 Tool Proxy） |
| `GET` `POST` | `/v1/knowledge/collections` | 知識集合 |
| `POST` | `/v1/knowledge/collections/{id}/documents` | 上傳文件，回 `ingestion_job_id` |
| `GET` | `/v1/knowledge/ingestion-jobs/{id}` | 索引進度：`PENDING`\|`PARSING`\|`CHUNKING`\|`EMBEDDING`\|`INDEXING`\|`COMPLETED`\|`FAILED` |
| `POST` | `/v1/knowledge/collections/{id}/search` | 檢索測試台，回 chunk + 分數 + 來源 |

---

## 5. 契約變更紀錄

| 版本 | 日期 | 變更 | 由 |
|---|---|---|---|
| v0.1.0 | 2026-09-20 | 初版：Runs / Agents / MCP / 錯誤枚舉 | @architect |
