# REST API（`/v1`）

前後端唯一的約定。Console 的欄位名只出現在 `apps/console/static/js/api.js`。
容器用的 `/internal/*` 看 [gateway.md](gateway.md)；MCP 看 [mcp.md](mcp.md)。

## 通則

| 項目 | 規則 |
|---|---|
| Base | `http://<host>/v1`，Console 同源 |
| 認證 | `/v1` 沒有平台層認證。唯一的 key 是 Agent 的下游 API key，只在建 Run 時檢查 |
| 時間 | RFC 3339 UTC |
| ID | UUID v7 |
| 分頁 | `?limit=50&cursor=…`，回應一定有 `next_cursor`（沒下一頁 = `null`） |
| 金額 | `cost_usd` 是字串（`"0.0182"`） |
| 多餘欄位 | request 帶未定義欄位 → `422 VALIDATION_ERROR` |
| OpenAPI | `/openapi.json`（不含 Console 與 `/internal/*`） |

### 錯誤格式（全平台唯一）

```json
{ "error": { "code": "SANDBOX_TIMEOUT", "message": "…", "run_id": "…", "retryable": true, "details": {} } }
```

`code` 只能用 `apps/api/enums.py::ErrorCode`：

| code | HTTP | 可重試 | 何時 |
|---|---|---|---|
| `UNAUTHENTICATED` | 401 | 否 | token / key 無效、過期、已撤銷 |
| `PERMISSION_DENIED` | 403 | 否 | 拿 A 的 token / key 打 B |
| `NOT_FOUND` | 404 | 否 | |
| `VALIDATION_ERROR` | 422 | 否 | `details` 指出欄位 |
| `QUOTA_EXCEEDED` | 429 | 是 | 一次性沙盒滿了 |
| `AGENT_NOT_PUBLISHED` | 409 | 否 | 沒有 `PUBLISHED` 版本 |
| `AGENT_NOT_RUNNING` | 409 | 否 | 常駐容器 10 分鐘內死 4 次被放棄。Run 仍建立（直接 `FAILED`），回應帶 `run_id` |
| `VERSION_CONFLICT` | 409 | 否 | 同版號驗證中；刪仍被引用的機密；出口規則 host 重複；改已撤銷的 MCP key |
| `BUILD_FAILED` | — | 否 | 版本驗證失敗 |
| `SANDBOX_TIMEOUT` | — | 是 | Run 逾時；同步呼叫等超過 60 秒 |
| `SANDBOX_ERROR` | — | 是 | 容器啟動失敗或崩潰 |
| `LLM_ERROR` | — | 是 | Gateway 打供應商失敗 |
| `EGRESS_BLOCKED` | 403 | 否 | 對外請求被政策擋，原因在 `details.reason` |
| `EGRESS_FAILED` | 502 | 是 | 政策放行但打不通 |
| `USER_CODE_ERROR` | — | 否 | Agent 程式拋例外 |
| `INTERNAL_ERROR` | 500 | 是 | |

HTTP 是 `—` 的只出現在 Run / 版本的 `error` 物件裡。

## Runs

### `POST /v1/agents/{agent_id}/runs` → `202`

```json
{ "entrypoint": "default", "input": {}, "version": "1.2.0", "config": { "timeout_seconds": 900, "metadata": {} } }
```

- `input` 必填，用該 entrypoint 的 `input_schema` 驗證。`entrypoint` 預設 `default`。
- `version` 省略 = 最新 `PUBLISHED`（semver 最大）；指定則用該版號現役那筆。
- `timeout_seconds` 預設 900，上限 900。
- 回應：`{run_id, agent_id, agent_version, status: "QUEUED", created_at, events_url}`。
- `trigger` 由伺服器決定：REST / Console = `API`，MCP = `MCP`。

### `POST /v1/agents/{agent_id}/runs:invoke` → `200`

同上，但等到結束才回（最多 60 秒），回完整 Run 物件。超時回 `SANDBOX_TIMEOUT`，Run 繼續跑。

### `GET /v1/runs/{run_id}`

```json
{ "run_id": "…", "agent_id": "…", "agent_version": "1.2.0", "status": "SUCCEEDED", "trigger": "API",
  "input": {}, "output": {}, "error": null,
  "usage": { "input_tokens": 1520, "output_tokens": 340, "llm_calls": 3, "duration_ms": 8420, "cost_usd": "0.0182" },
  "created_at": "…", "started_at": "…", "finished_at": "…" }
```

- `status`：`QUEUED` · `SCHEDULING` · `RUNNING` · `SUCCEEDED` · `FAILED` · `CANCELLED`（`apps/api/enums.py::RunStatus`）。
  在常駐容器佇列排隊時是 `SCHEDULING`；`started_at` 是被容器取走的時間。
- 沒值的欄位是 `null`，但 key 一定在。`usage` 由平台從 `llm_calls` 加總。

### `GET /v1/runs/{run_id}/events`（SSE）

`id` = `run_events.seq`，支援 `Last-Event-ID` 續傳。型別：`run.status` · `node.start` · `node.end` · `token` · `log` · `custom` · `error`。
`run.status` 只有平台產生；收到終態的 `run.status` 後串流關閉。

### `GET /v1/runs`

Query：`agent_id` · `api_key_id` · `status` · `trigger` · `created_after` · `created_before` · `limit` · `cursor`。精簡列（無 input / output），含 `agent_name`。

### `POST /v1/runs/{run_id}/cancel` —— 未實作（404）

## Agents

| Method | Path | 回應 | 說明 |
|---|---|---|---|
| `POST` | `/v1/agents` | `201` | `{name, description}`。`name` 唯一，重複 → `422` |
| `GET` | `/v1/agents` | `200` | `agent_id` · `name` · `description` · `latest_published_version` · `updated_at` |
| `GET` | `/v1/agents/{id}` | `200` | 上面 + `created_at` + `endpoints: {rest, openapi}` |
| `PATCH` | `/v1/agents/{id}` | `200` | 只能改 `name` / `description` |
| `DELETE` | `/v1/agents/{id}` | `204` | 硬刪，連帶所有版本、Run、稽核、key、設定（見 [agent.md](agent.md)） |
| `GET` | `/v1/agents/{id}/openapi.json` | `200` | 由 entrypoints 生成的 OpenAPI 3.1 |

`name`：英數字、`.`、`_`、`-`，首字英數，最長 255。

## 版本

| Method | Path | 回應 | 說明 |
|---|---|---|---|
| `POST` | `/v1/agents/{id}/versions` | `202` | `{semver, image_ref}`，背景驗證 |
| `GET` | `/v1/agents/{id}/versions` | `200` | 列表（含 `manifest` / `entrypoints`），不含被取代的 |
| `GET` | `/v1/agents/{id}/versions/{semver}` | `200` | 該版號最近一次註冊的那筆 |

```json
{ "agent_version_id": "…", "semver": "1.2.0", "version_id": "0123456789ab", "source_type": "OCI_IMAGE",
  "artifact": { "image_ref": "…@sha256:<64hex>", "digest": "sha256:<64hex>",
                "platform": null, "size_bytes": null, "runtime_contract": null },
  "status": "BUILDING", "error": null, "created_at": "…" }
```

- `image_ref` 只接受 `repository@sha256:<64hex>`，tag-only → `422`。
- `status`：`BUILDING` · `PUBLISHED` · `FAILED`（`SUPERSEDED` 只在 DB）。
- 失敗：`error.code = BUILD_FAILED`，`details.stage` ∈ `PULL` / `INSPECT` / `VALIDATE`。
- 同版號再送 = 取代。已有 `BUILDING` → `409`；`image_ref` 與現役相同 → `202` 回現役那筆。

## 常駐容器

| Method | Path | 回應 |
|---|---|---|
| `GET` | `/v1/agents/{id}/containers` | `200` |
| `POST` | `/v1/agents/{id}/containers`（body 可省略或 `{version}`） | `202` 預熱 |
| `POST` | `/v1/agents/{id}/containers/{cid}/restart` | `202` |
| `DELETE` | `/v1/agents/{id}/containers/{cid}` | `202` |

```json
{ "container_id": "…", "agent_id": "…", "agent_version": "1.2.0", "status": "READY",
  "inflight_runs": 3, "max_concurrent_runs": 50, "restart_count": 0,
  "started_at": "…", "last_health_at": "…", "error": null }
```

`status`：`STARTING` · `READY` · `UNHEALTHY` · `STOPPED` · `FAILED`。寫入類只登記想要的狀態，由 worker 執行。

## 下游 API key（REST 用）

| Method | Path | 回應 |
|---|---|---|
| `POST` | `/v1/agents/{id}/api-keys`，`{name}`（1–128） | `201`，明文 `key` 只出現這次 |
| `GET` | `/v1/agents/{id}/api-keys` | `200`，含已撤銷，不含 key |
| `DELETE` | `/v1/agents/{id}/api-keys/{key_id}` | `200`，帶 `revoked_at`（軟撤銷） |

```json
{ "key_id": "…", "agent_id": "…", "name": "…", "key": "zk_live_…", "key_prefix": "zk_live_a1b2",
  "created_at": "…", "last_used_at": null, "revoked_at": null }
```

建 Run 時 `Authorization: Bearer zk_live_…`：

| 請求 | 結果 |
|---|---|
| 沒帶 | 放行，`api_key_id = NULL` |
| 有效 key | 放行，記 `api_key_id` |
| 無效 / 已撤銷 | `401` |
| 別的 Agent 的 key | `403` |
| `ZENTRA_REQUIRE_AGENT_API_KEY=true` 且沒帶 | `401` |

## MCP key

| Method | Path | 回應 |
|---|---|---|
| `POST` | `/v1/mcp-keys`，`{name, agent_ids}` | `201`，明文 `key` 只出現這次；不存在的 Agent → `404` |
| `GET` | `/v1/mcp-keys` | `200`，`{endpoint, items}`，含已撤銷 |
| `PUT` | `/v1/mcp-keys/{key_id}/agents`，`{agent_ids}` | `200`，整份取代；已撤銷 → `409` |
| `DELETE` | `/v1/mcp-keys/{key_id}` | `200`，帶 `revoked_at` |

```json
{ "key_id": "…", "name": "…", "key": "zmcp_live_…", "key_prefix": "zmcp_live_a1b2",
  "agent_ids": ["…"], "created_at": "…", "last_used_at": null, "revoked_at": null }
```

## 用量

| Method | Path | 說明 |
|---|---|---|
| `GET` | `/v1/agents/{id}/usage` | `totals` · `by_key` · `by_model`。`by_key` 一定有 `key_id: null` 列 |
| `GET` | `/v1/agents/{id}/llm-calls` | 逐筆，可翻頁 |

共用 query：`api_key_id` · `since` · `until` · `model` · `credential_source`；`llm-calls` 另有 `status_code` · `errors_only` · `limit` · `cursor`。

```json
{ "call_id": "…", "run_id": "…", "run_status": "SUCCEEDED", "api_key_id": "…", "model": "gpt-4o",
  "input_tokens": 10, "output_tokens": 0, "cost_usd": "0", "latency_ms": 120, "status_code": 429,
  "error": "rate limited", "credential_source": "AGENT", "created_at": "…" }
```

## 每個 Agent 的憑證、設定、機密、白名單、寄件者

全部綁 Agent、不綁版本，改了下一筆 Run 生效。行為看 [gateway.md](gateway.md)。

### LLM 憑證 `/v1/agents/{id}/llm-credential`

- `PUT` `{api_key (8–512), base_url?, default_model?}`
- `GET` → `{configured, last_four, base_url, default_model, updated_at}`
- `DELETE` → `204`，退回平台預設金鑰
- 沒有端點能讀回金鑰。沒設 `ZENTRA_SECRET_KEY` → `PUT` 回 `422`。

### 設定 `GET` / `PUT /v1/agents/{id}/config`

`{config: {...}, updated_at}`，整份取代。鍵名 `^[A-Za-z_][A-Za-z0-9_]{0,63}$`，≤ 100 個頂層鍵，≤ 64 KiB。明文，會進容器。

### 機密 `/v1/agents/{id}/secrets`

- `GET` → `{items: [{name, last_four, created_at, updated_at}]}`
- `PUT /{name}` `{value}`（1–4096）→ `200`
- `DELETE /{name}` → `204`；被出口規則或寄件者引用 → `409`，`details.referenced_by`
- 名字規則同設定鍵名，每個 Agent ≤ 50。Fernet 加密，沒設 `ZENTRA_SECRET_KEY` → `422`。

### 對外白名單 `/v1/agents/{id}/egress-rules`

`GET` / `POST`（`201`）/ `PUT /{rule_id}`（整條取代）/ `DELETE /{rule_id}`（`204`）。

```json
{ "host": "api.binance.com", "methods": ["GET"], "path_prefix": "/api/v3/",
  "auth_secret_name": null, "auth_header": null, "description": "…" }
```

`422` 條件：
- `host` 要是小寫主機名或 IP；不能有 `*`、scheme、port、路徑。放行該主機所有 port。
- `host` 不能是 `postgres`、`redis`、`registry`、`app`、`worker`、`localhost`、`127.0.0.0/8`、`::1`。
- `methods` 只能 `GET` / `POST`，非空。
- `auth_secret_name` 與 `auth_header` 成對，機密要存在；`auth_header` 不能是 `Accept` / `Content-Type` / `Host` / `Content-Length`。
- 每個 Agent ≤ 20 條。同 Agent `host` 重複 → `409`。

### 寄件者 `GET` / `PUT` / `DELETE /v1/agents/{id}/email-sender`

```json
{ "smtp_host": "smtp.gmail.com", "smtp_port": 587, "username": "…", "from_address": "…",
  "password_secret_name": "SMTP_PASSWORD", "allowed_recipients": ["…"] }
```

回應多 `configured`、`updated_at`。帶 `password` → `422`。`allowed_recipients` 1–10 個完整地址。`smtp_port` ∈ 587 / 465 / 25。

### 對外請求稽核 `GET /v1/agents/{id}/egress-calls`

Query：`kind`（`HTTP` / `EMAIL`）· `run_id` · `since` · `until` · `host` · `status_code` · `errors_only` · `limit` · `cursor`。

```json
{ "call_id": "…", "run_id": "…", "kind": "HTTP", "rule_id": "…", "method": "GET",
  "host": "api.binance.com", "path": "/api/v3/klines", "status_code": 200,
  "request_bytes": 0, "response_bytes": 18422, "latency_ms": 310, "error": null, "created_at": "…" }
```

`path` 不含 query string。`kind=EMAIL` 時 `host` = SMTP 主機、`path` = 收件人。
