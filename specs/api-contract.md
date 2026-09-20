# API 契約 (API Contract)

| 欄位 | 值 |
|---|---|
| 契約版本 | `v0.2.2` |
| 擁有者 | `@architect` |
| 對齊 Spec | `specs/SPEC.md v0.2.2` |

> **這是前端與後端的唯一交會點。**
> 後端照此實作，前端照此 mock，QA 照此寫契約測試。
> **要改 → 開 `SPEC-QUESTION`，架構師裁決後 bump 版本，並同時通知三方。**
> 前端不准猜欄位名，後端不准自創回應格式。

> **v0.2.0 依 [ADR-0005](./adr/ADR-0005-mvp-single-host-scope.md) 降維**：移除 Tools / Knowledge 端點群、
> 移除 `POST /runs/{id}/resume`、狀態枚舉移除 `WAITING_INPUT`、`config.tier` 移除；
> 新增 §4 Internal Gateway（沙盒專用內部介面）。
>
> **v0.2.1 依 [ADR-0006](./adr/ADR-0006-vanilla-frontend.md)**：Console 與 API **同源**，
> 直接消費這份契約（沒有 BFF、沒有 CORS）。**端點本身沒有任何變更** ——
> 但這代表 Console 是這份契約的第一個真實消費者，欄位對不上做畫面時就會發現。

> **v0.2.2 澄清 ZEN-000 所需的 Internal Gateway 內容格式與 Run 終態權責**：
> `events` 的 request body 是事件陣列，`result` 只回寫資料；worker 才能在沙盒退出後決定終態。

---

## 0. 通則

- Base URL：`http://<host>/v1`（單機部署，MVP 不強制 TLS）。**Console 同源，用相對路徑 `/v1`**
- 認證：`Authorization: Bearer <api_key>`。MVP 未實作（ZEN-000 明確不做）；
  補上時 Console 走**同源 session cookie**，外部系統走 api_key —— 見 [ADR-0006](./adr/ADR-0006-vanilla-frontend.md) §四
- `api` 服務同時提供 Console 靜態檔（`/console/*`、`/console/static/*`）與 `/` 轉址。
  那些是 UI 路由，不是 API，規格見 [`ui-contract.md`](./ui-contract.md) 表頭
- 所有時間：RFC 3339 UTC（`2026-09-20T06:30:00Z`）
- 所有 ID：UUID v7（可排序）
- 分頁：cursor-based，`?limit=50&cursor=<opaque>`，回應含 `next_cursor`（無下一頁則 `null`）
- 冪等：`Idempotency-Key` header 的 24 小時重送保證由 ZEN-004 交付；
  ZEN-000 只要求同一 `run_id` 的 Celery 重送與 Internal Gateway 事件重送冪等

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
| `NOT_FOUND` | 404 | false | 資源不存在 |
| `VALIDATION_ERROR` | 422 | false | 請求格式錯誤，`details` 含欄位層級訊息 |
| `QUOTA_EXCEEDED` | 429 | true | **超過併發 Run 上限（預設 10）**，含 `Retry-After` |
| `AGENT_NOT_PUBLISHED` | 409 | false | Agent 版本未發布 |
| `BUILD_FAILED` | — | false | 版本發布失敗（寫在 `agent_versions.error`） |
| `SANDBOX_TIMEOUT` | — | true | 沙盒執行逾時（寫在 Run 的 error，非 HTTP） |
| `SANDBOX_ERROR` | — | true | 沙盒啟動或崩潰 |
| `LLM_ERROR` | — | true | Internal Gateway 呼叫模型供應商失敗 |
| `EGRESS_BLOCKED` | — | false | 沙盒嘗試連 Internal Gateway 以外的位址 |
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
    "metadata": { "trace_id": "..." }
  }
}
```
| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| `entrypoint` | string | 否 | 預設 `"default"` |
| `input` | object | 是 | 由該 entrypoint 的 schema 驗證 |
| `version` | string | 否 | 省略則用**最新的 `PUBLISHED` 版本** |
| `config.timeout_seconds` | int | 否 | 預設 900，**硬上限 900**（SPEC §10） |

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
    "llm_calls": 3,
    "duration_ms": 8420,
    "cost_usd": "0.0182"
  },
  "created_at": "...", "started_at": "...", "finished_at": "..."
}
```
| 欄位 | 值域 |
|---|---|
| `status` | `QUEUED` \| `SCHEDULING` \| `RUNNING` \| `SUCCEEDED` \| `FAILED` \| `CANCELLED` — **照抄 SPEC §9，不得自創** |
| `trigger` | `API` \| `MCP` \| `CONSOLE` |
| `cost_usd` | **字串**（decimal，避免浮點誤差） |

`output`、`error`、`usage`、`started_at`、`finished_at` 在尚未產生時為 `null`，欄位仍須出現。
`SUCCEEDED` 時 `output` 為物件、`error` 為 `null`；`FAILED` 時 `error.code` 必須是 §0 枚舉值。
不呼叫 LLM 的 Run，其 `usage.llm_calls`、`usage.input_tokens`、`usage.output_tokens` 為 `0`，
`usage.cost_usd` 為 decimal 字串 `"0"`。

> `WAITING_INPUT` 已於 v0.2.0 移除。前端不得渲染、後端不得寫入、QA 不得斷言這個值。

### `GET /v1/runs/{run_id}/events` — SSE 串流
Header：`Accept: text/event-stream`，支援 `Last-Event-ID` 續傳（事件持久化於 `run_events` 表）。

```
id: 42
event: node.start
data: {"node":"llm_node","ts":"..."}

id: 43
event: token
data: {"node":"llm_node","delta":"這"}

id: 44
event: log
data: {"level":"info","message":"fetched 12 rows"}

id: 45
event: custom
data: {"name":"progress","payload":{"pct":60}}

id: 46
event: run.status
data: {"status":"SUCCEEDED"}
```
事件型別枚舉：`run.status`｜`node.start`｜`node.end`｜`token`｜`log`｜`custom`｜`error`

> `tool.call` / `tool.result` / `interrupt` 於 v0.2.0 移除（無 Tool Registry、無 HITL）。
> 使用者要推自訂進度用 `ctx.emit()` → `custom` 事件。

> **前端必須處理**：重連（帶 `Last-Event-ID`）、重複事件去重（用 `id`）、離開頁面關閉連線。

### `POST /v1/runs/{run_id}/cancel` → `200`，status 轉 `CANCELLED`
僅在 `QUEUED` / `SCHEDULING` / `RUNNING` 有效，否則 `409`。沙盒立即銷毀。

### `GET /v1/runs` — 列表
Query：`agent_id`、`status`、`trigger`、`created_after`、`created_before`、`limit`、`cursor`
```json
{ "items": [ /* Run 精簡物件，不含 input/output */ ], "next_cursor": null }
```

---

## 2. Agents 與版本

| Method | Path | 說明 |
|---|---|---|
| `GET` | `/v1/agents` | 列表 |
| `POST` | `/v1/agents` | 建立 Agent（只建 metadata，還沒有版本） |
| `GET` | `/v1/agents/{id}` | 詳情，含 `endpoints` |
| `PATCH` | `/v1/agents/{id}` | 改 metadata（**不可改已發布版本**，P5） |
| `DELETE` | `/v1/agents/{id}` | 軟刪除 |
| `GET` | `/v1/agents/{id}/versions` | 版本列表 |
| `POST` | `/v1/agents/{id}/versions` | **上傳 bundle 並發布**（見下） |
| `GET` | `/v1/agents/{id}/versions/{semver}` | 版本詳情，含 `entrypoints` 與 build 錯誤 |
| `GET` | `/v1/agents/{id}/openapi.json` | 該 Agent 的 OpenAPI 3.1 schema（P3） |

> `PUT /v1/agents/{id}/aliases/{alias}`（流量切換）於 v0.2.0 移除。
> 呼叫時不指定 `version` 即用最新 `PUBLISHED` 版本。

### `POST /v1/agents/{id}/versions` — 上傳並發布

`multipart/form-data`：`bundle`（`.tar.gz`）+ `semver`（string）

Response `202`：
```json
{
  "agent_version_id": "0192...",
  "semver": "1.2.0",
  "version_id": "a3f9c1e04b77",
  "status": "BUILDING",
  "created_at": "..."
}
```
| 欄位 | 說明 |
|---|---|
| `version_id` | **bundle 的 SHA-256 前 12 碼**。P5 的識別依據 |
| `status` | `BUILDING` \| `PUBLISHED` \| `FAILED` |

發布是非同步的（`q.build`）。前端輪詢 `GET /v1/agents/{id}/versions/{semver}` 看 `status`。
同一個 `semver` 重複上傳 → `409`（P5：版本不可變）。

### Agent 詳情的 `endpoints`（P3 的體現）
```json
{
  "endpoints": {
    "rest": "http://localhost:8000/v1/agents/0192.../runs",
    "mcp": "http://localhost:8000/mcp/agents/0192...",
    "openapi": "http://localhost:8000/v1/agents/0192.../openapi.json"
  }
}
```

---

## 3. MCP 介面

端點：`http://<host>/mcp/agents/{agent_id}`（Streamable HTTP，MCP 標準協定）

| MCP 方法 | 對應行為 |
|---|---|
| `tools/list` | 回傳該 Agent 每個 entrypoint 一個 tool，schema 由 manifest 生成 |
| `tools/call` | 觸發一次 Run。預設同步等待（上限 60s）；`_async: true` 則回 `run_id` |

> `resources/list`、`resources/read` 於 v0.2.0 移除（它們對應知識庫，不在 MVP）。

> **P3 的實作要求**：`tools/list` 的 input schema 與 REST 的 `input` schema **必須由同一份 entrypoint 定義生成**。
> 契約測試要驗證兩者等價 —— 這是 QA 的必測項。

---

## 4. Internal Gateway（沙盒使用的非公開契約）

> 這一段給 `@platform-eng` 與 `@backend-dev`，**不是公開 API 契約，也不列入公開 OpenAPI**。
> 它是 P4 的實作：沙盒唯一的網路對端，代持所有長期憑證。
> 前端永遠不呼叫這些端點。

MVP 的 `/internal/*` 與 `/v1/*` 掛在同一 FastAPI app，主機映射的 `api:8000` 也可到達這些路由；
「非公開」**不表示已有來源網段封鎖**。每一個 `/internal/runs/*` 請求都必須驗證 run token，
這是 ZEN-000 的存取控制。不得因為路由名稱是 `internal` 就省略認證。

認證：`Authorization: Bearer <run_token>`。run token 綁定單一 `run_id`，TTL = 該 Run 的 `timeout_seconds`，
Run 結束立即撤銷。**帶著 A 的 token 存取 B 的 run_id → `403 PERMISSION_DENIED`。**

| Method | Path | 說明 |
|---|---|---|
| `GET` | `/internal/runs/{run_id}/bootstrap` | 取 manifest、entrypoint 名稱、input |
| `POST` | `/internal/runs/{run_id}/events` | 回寫事件（批次，body 是事件陣列） |
| `POST` | `/internal/runs/{run_id}/result` | 回寫最終 output 或 error |
| `POST` | `/internal/llm/v1/chat/completions` | **OpenAI 相容**。平台換上真金鑰、計量 token、寫 `llm_calls` |
| `POST` | `/internal/versions/{version_id}/validation` | `--mode=validate` 沙盒回報驗證結果與抽出的 entrypoints schema |

ZEN-000 用到的三條路由內容格式如下；這些是沙盒與平台間的契約，公開 `/v1` 的欄位與狀態不因骨架而縮減：

| 路由 | Request | 成功 Response |
|---|---|---|
| `GET /internal/runs/{run_id}/bootstrap` | 無 body | `200`：`{"run_id":"<uuid>","entrypoint":"default","input":{},"manifest":{}}` |
| `POST /internal/runs/{run_id}/events` | **頂層 JSON 陣列**：`[{"event_id":"01930000-0000-7000-8000-000000000003","type":"node.start","payload":{"node":"echo_node","ts":"..."}}]`；沙盒可送 `node.start`、`node.end`、`token`、`log`、`custom`、`error`，`run.status` 只由平台產生 | `202`：`{"accepted":1,"last_seq":42}`；`accepted` 是新寫入的事件數，`last_seq` 是此批最後一筆（含重送）的 SSE id，空陣列時為 `null` |
| `POST /internal/runs/{run_id}/result` | `{"output":{},"error":null,"usage":{}}`；失敗時 `output=null` 且 `error.code` 為 §0 枚舉值 | `202`：`{"accepted":true}` |

`result` 只保存沙盒回報的 output／error／usage，不轉 Run 狀態，也不發終局 `run.status`。
worker 以沙盒退出狀態與已保存結果為依據，唯一決定 `SUCCEEDED` 或 `FAILED`，
在同一 DB transaction 將終態寫入 `runs` 並新增唯一的終局 `run.status` 事件，
提交後發布 Redis 通知；之後撤銷 token 與銷毀沙盒。重送 worker task 要先檢查原終局事件並補送 Redis 通知，
不能因 Run 已是終態就略過尚未發布的事件。
沙盒未回寫結果就退出，不能推定成功。重複派工不能同時建立第二個沙盒或重複發出終局事件（ADR-0002）。

沙盒生成每筆事件的 `event_id`（UUID v7，於同一 Run 內唯一）；**同一事件重送時沿用原值**。
`run_events` 對 `(run_id, event_id)` 設唯一約束。Gateway 先持久化 Postgres，再 `XADD` 到 Redis；
若 DB commit 成功而 `XADD` 失敗，回報暫時失敗，沙盒用相同 `event_id` 重試。
重試查回原事件與原 `seq`，不得再新增 `run_events`；Redis 重複通知可能發生，
SSE 以持久化 `seq` 去重並依 `seq` 排序。公開 SSE 的 `id` 仍是資料庫 `seq`，不是 `event_id`。
平台產生的 `run.status` 不走沙盒事件端點；其終局唯一性由終態轉換的 DB transaction 保證。

`/internal/llm/v1/chat/completions` 刻意做成 OpenAI 相容，
`ctx.llm()` 因此只是一個設了 `base_url` 與 `api_key=run_token` 的標準 LangChain chat model——SDK 不需要自訂 client。

---

## 5. 契約變更紀錄

| 版本 | 日期 | 變更 | 由 |
|---|---|---|---|
| v0.2.2 | 2026-09-21 | 明定 `GET Run` 未產生欄位的 null 語意、無 LLM 用量；§4 補上 bootstrap/events/result schema、事件 `event_id` 冪等與 worker 唯一決定終態；澄清 `/internal/*` 可由主機埠到達且必驗 token。公開端點和枚舉未新增 | @architect |
| v0.1.0 | 2026-09-20 | 初版：Runs / Agents / MCP / 錯誤枚舉 | @architect |
| v0.2.1 | 2026-09-20 | Console 改為同源消費本契約（[ADR-0006](./adr/ADR-0006-vanilla-frontend.md)）：§0 補上同源 / 認證 / 靜態檔路由說明。**端點、欄位、枚舉全部未變** | @architect |
| v0.2.0 | 2026-09-20 | **降維改版**（ADR-0005）：移除 Tools / Knowledge 端點群、`runs/{id}/resume`、alias 切換、`config.tier`、MCP `resources/*`；狀態枚舉移除 `WAITING_INPUT`；事件枚舉移除 `tool.*` / `interrupt`，新增 `log` / `custom`；錯誤碼移除 `TOOL_ERROR`，新增 `LLM_ERROR` / `BUILD_FAILED`；版本上傳改為 multipart 直傳 + `version_id` = bundle SHA-256；新增 §4 Internal Gateway | @architect |
