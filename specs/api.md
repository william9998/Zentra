# API 規格（對外 REST `/v1`）

> 前端與後端唯一的交會點。**前端不准猜欄位名，後端不准自創回應格式。**
> 欄位名在 Console 只准出現在 `apps/console/static/js/api.js`。
> 沙盒用的 `/internal/*` 不在這裡，見 [gateway.md](gateway.md)；MCP 見 [mcp.md](mcp.md)。

## 通則

- Base：`http://<host>/v1`。Console 同源，用相對路徑 `/v1`，沒有 CORS。
- 認證：**`/v1` 沒有平台層認證**（單租戶內部工具）。唯一的 key 是 Agent 的下游 API key，只在建立 Run 時檢查（見 §API key）。
- 時間：RFC 3339 UTC。ID：UUID v7（可排序）。
- 分頁：cursor，`?limit=50&cursor=<opaque>`；回應**一定有** `next_cursor` 這個 key，沒有下一頁是 `null`。
  游標是 `created_at + id` 的 base64。
- 金額：`cost_usd` 一律是**十進位字串**（`"0.0182"`），不是數字。前端只印不算。
- 未定義的 request 欄位 → `422 VALIDATION_ERROR`，不默默忽略。
- `/v1` 的 OpenAPI 在 `/openapi.json`；Console 路由與 `/internal/*` 不列入（`include_in_schema=False`）。

### 錯誤格式（全平台唯一）

```json
{ "error": { "code": "SANDBOX_TIMEOUT", "message": "人類可讀", "run_id": "0192…", "retryable": true, "details": {} } }
```

### 錯誤 code（`apps/api/enums.py::ErrorCode`，不得自創）

| code | HTTP | retryable | 意思 |
|---|---|---|---|
| `UNAUTHENTICATED` | 401 | false | token / key 無效、過期或已撤銷 |
| `PERMISSION_DENIED` | 403 | false | token 權限不符（例如 run A 的 token 打 run B、別的 Agent 的 key） |
| `NOT_FOUND` | 404 | false | 資源不存在 |
| `VALIDATION_ERROR` | 422 | false | 請求格式錯，`details` 指出欄位 |
| `QUOTA_EXCEEDED` | 429 | true | 一次性沙盒併發滿了（預設 10） |
| `AGENT_NOT_PUBLISHED` | 409 | false | 沒有可用的 `PUBLISHED` 版本 |
| `AGENT_NOT_RUNNING` | 409 | false | 已定義，**目前沒有路徑會回它**（常駐容器起不來會退回一次性沙盒） |
| `VERSION_CONFLICT` | 409 | false | 同版號正在驗證中；也用於：刪除仍被引用的機密、出口規則 host 重複 |
| `BUILD_FAILED` | — | false | 版本驗證失敗（寫在版本的 `error`） |
| `SANDBOX_TIMEOUT` | — | true | Run 逾時；同步呼叫等超過 60 秒 |
| `SANDBOX_ERROR` | — | true | 容器啟動失敗或崩潰 |
| `LLM_ERROR` | — | true | Gateway 打模型供應商失敗 |
| `EGRESS_BLOCKED` | 403 | false | 出口請求被平台政策擋下，原因在 `details.reason` |
| `EGRESS_FAILED` | 502 | true | 政策放行但上游打不通 |
| `USER_CODE_ERROR` | — | false | 使用者程式碼拋例外 |
| `INTERNAL_ERROR` | 500 | true | 未預期錯誤 |

HTTP 欄為 `—` 的只出現在 Run 或版本的 `error` 物件裡，不是 HTTP 回應。

## Runs

### `POST /v1/agents/{agent_id}/runs` → `202`

```json
{ "entrypoint": "default", "input": { "question": "…" }, "version": "1.2.0",
  "config": { "timeout_seconds": 900, "metadata": {} } }
```

| 欄位 | 必填 | 說明 |
|---|---|---|
| `entrypoint` | 否 | 預設 `default` |
| `input` | 是 | 以該 entrypoint 的 `input_schema` 驗證 |
| `version` | 否 | 省略 = 最新的 `PUBLISHED`（依 semver 取最大）。指定時用該版號**現役**的那一筆 |
| `config.timeout_seconds` | 否 | 預設 900，**硬上限 900** |

```json
{ "run_id": "…", "agent_id": "…", "agent_version": "1.2.0", "status": "QUEUED",
  "created_at": "…", "events_url": "/v1/runs/…/events" }
```

`trigger` 由伺服器依入口固定寫入（REST 與 Console 都是 `API`，MCP 是 `MCP`），body 或任何 header 都改不了。

### `POST /v1/agents/{agent_id}/runs:invoke` → `200`

同上的 request，同步等終態，上限 **60 秒**，超過回 `SANDBOX_TIMEOUT`。回應是完整 Run 物件。

### `GET /v1/runs/{run_id}`

```json
{ "run_id": "…", "agent_id": "…", "agent_version": "1.2.0", "status": "SUCCEEDED", "trigger": "API",
  "input": {}, "output": {}, "error": null,
  "usage": { "input_tokens": 1520, "output_tokens": 340, "llm_calls": 3, "duration_ms": 8420, "cost_usd": "0.0182" },
  "created_at": "…", "started_at": "…", "finished_at": "…" }
```

- `status`：`QUEUED | SCHEDULING | RUNNING | SUCCEEDED | FAILED | CANCELLED`（狀態機見 [dispatch.md](dispatch.md)）。**沒有 `WAITING_INPUT`**，不要寫也不要斷言。
- `output` / `error` / `usage` / `started_at` / `finished_at` 還沒有值時是 `null`，**key 一定在**。
- 不打 LLM 的 Run：`usage` 的計數為 `0`、`cost_usd` 為 `"0"`。`usage` 由 `llm_calls` 聚合，不採信沙盒回報。
- `agent_version` 是 semver。同版號可能被取代過，要知道實際跑的 image 看版本紀錄的 digest。

### `GET /v1/runs/{run_id}/events` — SSE

支援 `Last-Event-ID` 續傳。`id` 是資料庫的 `run_events.seq`。

```
id: 42
event: node.start
data: {"node":"llm_node","ts":"…"}
```

事件型別：`run.status` · `node.start` · `node.end` · `token` · `log` · `custom` · `error`。
`run.status` 只由平台產生；收到終局的 `run.status` 後串流結束。
前端必須：用 `id` 去重、離開頁面關閉連線（`EventSource` 自己會帶 `Last-Event-ID` 重連）。

### `GET /v1/runs`

Query：`agent_id` · `api_key_id` · `status` · `trigger` · `created_after` · `created_before` · `limit` · `cursor`。
每一列是精簡 Run（不含 input/output），含 `agent_id` **與 `agent_name`**。

### ⚠️ `POST /v1/runs/{run_id}/cancel` —— 未實作

Console 的 `api.js` 有呼叫它，但**後端沒有這條路由**（會 404）。要做的話：只在 `QUEUED` / `SCHEDULING` / `RUNNING` 有效，否則 `409`；轉 `CANCELLED` 並銷毀沙盒／從常駐容器撤回。

## Agents

| Method | Path | 回應 | 說明 |
|---|---|---|---|
| `POST` | `/v1/agents` | `201` | `{"name", "description"}`。`name` 全表唯一，重複 → `422` |
| `GET` | `/v1/agents` | `200` | 列表：`agent_id` · `name` · `description` · `latest_published_version` · `updated_at` |
| `GET` | `/v1/agents/{id}` | `200` | 上述 + `created_at` + `endpoints` |
| `PATCH` | `/v1/agents/{id}` | `200` | 只改 `name` / `description` |
| `DELETE` | `/v1/agents/{id}` | `204` | **硬刪，不可復原**（見 [agent-registry.md](agent-registry.md) §刪除） |
| `GET` | `/v1/agents/{id}/openapi.json` | `200` | 該 Agent 的 OpenAPI 3.1，由 entrypoints 生成（P3） |

```json
"endpoints": { "rest": "http://…/v1/agents/{id}/runs", "mcp": "http://…/mcp/agents/{id}", "openapi": "http://…/v1/agents/{id}/openapi.json" }
```

⚠️ 已知缺陷（BOARD ZEN-016）：`GET /v1/agents` 最後一頁缺 `next_cursor`、`GET /v1/agents/{id}` 值為 null 時缺 `description` / `latest_published_version` 欄位（`response_model_exclude_none` 造成）。契約要求 key 一定在。

## 版本

| Method | Path | 回應 | 說明 |
|---|---|---|---|
| `POST` | `/v1/agents/{id}/versions` | `202` | `{"semver", "image_ref"}` 註冊並非同步驗證 |
| `GET` | `/v1/agents/{id}/versions` | `200` | 列表（含 `manifest` / `entrypoints`）。被取代的不出現 |
| `GET` | `/v1/agents/{id}/versions/{semver}` | `200` | 這個版號**最近一次註冊**的那筆（註冊後輪詢靠它） |

```json
{ "agent_version_id": "…", "semver": "1.2.0", "version_id": "0123456789ab", "source_type": "OCI_IMAGE",
  "artifact": { "image_ref": "ghcr.io/acme/a@sha256:<64hex>", "digest": "sha256:<64hex>",
                "platform": null, "size_bytes": null, "runtime_contract": null },
  "status": "BUILDING", "error": null, "created_at": "…" }
```

- `image_ref` 只接受 `repository@sha256:<64 hex>`；tag-only、multipart、任何多餘欄位 → `422`。
- `status`：`BUILDING | PUBLISHED | FAILED`（`SUPERSEDED` 只在資料庫，不會出現在任何回應）。
- `version_id`、`artifact.image_ref`、`artifact.digest` 任何狀態都非 `null`；`platform` / `size_bytes` / `runtime_contract` / `manifest` / `entrypoints` 在 `BUILDING` 為 `null`，`PUBLISHED` 必有值。
- `FAILED`：`error.code = BUILD_FAILED`，`error.details.stage ∈ {PULL, INSPECT, VALIDATE}`。
- 同版號再送 = 取代；同版號已在 `BUILDING` → `409`；`image_ref` 與現役相同 → `202` 回現役那筆。完整規則見 [agent-registry.md](agent-registry.md)。

## 常駐容器

| Method | Path | 回應 | 說明 |
|---|---|---|---|
| `GET` | `/v1/agents/{id}/containers` | `200` | 這個 Agent 的容器列表 |
| `POST` | `/v1/agents/{id}/containers` | `202` | 預熱。body 可省略或 `{"version": "1.2.0"}`，省略用最新 `PUBLISHED` |
| `POST` | `/v1/agents/{id}/containers/{cid}/restart` | `202` | 重啟，`restart_count + 1` |
| `DELETE` | `/v1/agents/{id}/containers/{cid}` | `202` | 停止 |

```json
{ "container_id": "…", "agent_id": "…", "agent_version": "1.2.0", "status": "READY",
  "inflight_runs": 3, "max_concurrent_runs": 30, "restart_count": 0,
  "started_at": "…", "last_health_at": "…", "error": null }
```

`status`：`STARTING | READY | UNHEALTHY | STOPPED | FAILED`。寫入端點只登記期望狀態，實際動作由 worker 做（見 [container-orchestration.md](container-orchestration.md)）。

## API key（下游應用用）

| Method | Path | 回應 |
|---|---|---|
| `POST` | `/v1/agents/{id}/api-keys` `{"name"}`（1–128 字） | `201`，**`key` 明文只在這次出現** |
| `GET` | `/v1/agents/{id}/api-keys` | `200`，**含已撤銷的**，永不含 key |
| `DELETE` | `/v1/agents/{id}/api-keys/{key_id}` | `200` 帶 `revoked_at`（軟撤銷，冪等） |

```json
{ "key_id": "…", "agent_id": "…", "name": "行銷站", "key": "zk_live_…", "key_prefix": "zk_live_a1b2",
  "created_at": "…", "last_used_at": null, "revoked_at": null }
```

使用：`Authorization: Bearer zk_live_…`，REST 建 Run 與 MCP 同一套規則。設計見 [agent-registry.md](agent-registry.md) §下游 API key。

## 用量與除錯

| Method | Path | 說明 |
|---|---|---|
| `GET` | `/v1/agents/{id}/usage` | `totals` · `by_key` · `by_model` |
| `GET` | `/v1/agents/{id}/llm-calls` | 逐筆 LLM 呼叫，可翻頁 |

共用 query：`api_key_id` · `since` · `until` · `model` · `credential_source`；`llm-calls` 另有 `status_code` · `errors_only` · `limit` · `cursor`。

- `by_key` **一定含 `key_id: null` 那一列**（沒帶 key 的呼叫，含 Console）。
- 歸戶靠 `runs.api_key_id` join，`llm_calls` 沒有自己的 key 欄位。
- `errors_only=true` = `error IS NOT NULL OR status_code >= 400`。

```json
{ "call_id": "…", "run_id": "…", "run_status": "SUCCEEDED", "api_key_id": "…", "model": "gpt-4o",
  "input_tokens": 10, "output_tokens": 0, "cost_usd": "0", "latency_ms": 120, "status_code": 429,
  "error": "rate limited", "credential_source": "AGENT", "created_at": "…" }
```

## 每個 Agent 的憑證、設定、機密、出口

全部**綁 Agent 不綁版本**：發新版自動沿用，改了所有版本下一筆 Run 立刻生效。行為細節見 [gateway.md](gateway.md)。

| 層 | 讀得回來嗎 | 進得了沙盒嗎 |
|---|---|---|
| LLM 憑證 | 只有尾四碼 | 不會（Gateway 代打） |
| 設定 `config` | **明文** | 會，每筆 Run 進 `ctx.config` |
| 機密 `secrets` | 只有尾四碼 | **永遠不會**，沙盒只拿名字清單 |
| 出口白名單、寄件者 | 是（不含密碼） | 規則不進沙盒 |

### LLM 憑證

| Method | Path | 說明 |
|---|---|---|
| `PUT` | `/v1/agents/{id}/llm-credential` | `{"api_key"(8–512 字), "base_url"?, "default_model"?}` |
| `GET` | `/v1/agents/{id}/llm-credential` | `{"configured", "last_four", "base_url", "default_model", "updated_at"}`；未設定時 `configured:false`、其餘 `null` |
| `DELETE` | `/v1/agents/{id}/llm-credential` | `204`，退回平台預設金鑰 |

**沒有任何端點回傳金鑰明文或密文**（契約禁止，不是沒做）。沒設 `ZENTRA_SECRET_KEY` 時 `PUT` 回 `422`，不退回明文儲存。

### 設定

`GET` / `PUT /v1/agents/{id}/config`，body 與回應都是 `{"config": {...}, "updated_at"}`。

- 只有**整份取代**，沒有 PATCH。未設定過：`config: {}`、`updated_at: null`。
- 鍵名 `^[A-Za-z_][A-Za-z0-9_]{0,63}$`，最多 100 個頂層鍵，值是任意 JSON，整份 ≤ 64 KiB。
- **設定不是機密**：明文回傳、完整進沙盒。密碼、token 放機密。

### 機密

| Method | Path | 說明 |
|---|---|---|
| `GET` | `/v1/agents/{id}/secrets` | `{"items": [{"name", "last_four", "created_at", "updated_at"}]}` |
| `PUT` | `/v1/agents/{id}/secrets/{name}` | `{"value"}`（1–4096 字）。新建與覆寫都回 `200` |
| `DELETE` | `/v1/agents/{id}/secrets/{name}` | `204`，硬刪，冪等。仍被出口規則或寄件者引用 → `409`，`details.referenced_by` 列出引用者 |

- 名字規則同設定鍵名，每個 Agent 最多 50 個。Fernet 加密（要解得回來），沒設 `ZENTRA_SECRET_KEY` → `422`。
- `last_four`：長度 ≥ 4 是最後四字；< 4 是 `*` 重複該長度次。

### 出口白名單

| Method | Path | 回應 |
|---|---|---|
| `GET` | `/v1/agents/{id}/egress-rules` | `200` |
| `POST` | `/v1/agents/{id}/egress-rules` | `201` |
| `PUT` | `/v1/agents/{id}/egress-rules/{rule_id}` | `200`，整條取代 |
| `DELETE` | `/v1/agents/{id}/egress-rules/{rule_id}` | `204` |

```json
{ "host": "api.binance.com", "methods": ["GET"], "path_prefix": "/api/v3/",
  "auth_secret_name": null, "auth_header": null, "description": "Binance 現貨行情" }
```

違反任何一條 → `422`：`host` 只能是小寫 DNS 名稱（不含 `*`、scheme、port、路徑、IP）；`methods` ⊂ `{GET, POST}` 且非空；
`auth_secret_name` 與 `auth_header` 成對且機密要存在；`auth_header` 不得是 `Accept` / `Content-Type` / `Host` / `Content-Length`；每 Agent 最多 20 條。
同一 Agent 內 `host` 重複 → `409`。

### 寄件者

`GET` / `PUT` / `DELETE /v1/agents/{id}/email-sender`

```json
{ "smtp_host": "smtp.gmail.com", "smtp_port": 587, "username": "me@gmail.com", "from_address": "me@gmail.com",
  "password_secret_name": "SMTP_PASSWORD", "allowed_recipients": ["me@gmail.com"] }
```

回應多 `configured` 與 `updated_at`。**沒有密碼欄位**，body 帶 `password` → `422`（密碼只能以機密名字指定）。
`allowed_recipients` 必填、1–10 個精確地址；`smtp_port ∈ {587, 465, 25}`，一律要求 TLS。

### 出口稽核

`GET /v1/agents/{id}/egress-calls`，query：`kind`（`HTTP` | `EMAIL`）· `run_id` · `since` · `until` · `host` · `status_code` · `errors_only` · `limit` · `cursor`。

```json
{ "call_id": "…", "run_id": "…", "kind": "HTTP", "rule_id": "…", "method": "GET",
  "host": "api.binance.com", "path": "/api/v3/klines", "status_code": 200,
  "request_bytes": 0, "response_bytes": 18422, "latency_ms": 310, "error": null, "created_at": "…" }
```

`path` 不含 query string；`kind=EMAIL` 時 `path` 是收件人、`host` 是 SMTP 主機。`error` 的形狀見 [gateway.md](gateway.md) §稽核。

## 沒有實作的（不要假設存在）

- `POST /v1/runs/{id}/cancel`（見上）
- `Idempotency-Key` header 的重送保證
- `/v1` 的平台層認證（session / api_key）
