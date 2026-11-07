# REST API（`/v1`）

> 前端和後端**唯一**的約定。前端不准猜欄位名，後端不准自創回應格式。
> Console 裡欄位名只准出現在 `apps/console/static/js/api.js`。
> 容器用的 `/internal/*` 不在這份，看 [gateway.md](gateway.md)；MCP 看 [mcp.md](mcp.md)。

## 最快上手：三步跑完一次 Agent

```bash
# 1. 找到 Agent 的 id
curl -s localhost:8000/v1/agents
#    → {"items":[{"agent_id":"01a1…","name":"stock-agent","latest_published_version":"1.0.0",…}],"next_cursor":null}

# 2. 建一筆 Run（立刻回 202，不等結果）
curl -s -X POST localhost:8000/v1/agents/01a1…/runs \
  -H 'content-type: application/json' \
  -d '{"entrypoint":"default","input":{"question":"台積電最近怎麼樣"}}'
#    → {"run_id":"01a2…","status":"QUEUED","events_url":"/v1/runs/01a2…/events",…}

# 3. 查結果（或用 events_url 接 SSE 即時看進度）
curl -s localhost:8000/v1/runs/01a2…
#    → {"status":"SUCCEEDED","output":{…},"usage":{…},…}
```

想一次拿到結果（最多等 60 秒），把第 2 步的網址換成 `…/runs:invoke`。

## 通則

| 項目 | 規則 |
|---|---|
| Base | `http://<host>/v1`。Console 同源，用相對路徑 `/v1`，沒有 CORS |
| 認證 | **`/v1` 沒有平台層認證**（單租戶內部工具）。唯一的 key 是 Agent 的下游 API key，只在建 Run 時檢查（見 §API key） |
| 時間 | RFC 3339 UTC，例如 `2026-10-22T03:46:12Z` |
| ID | UUID v7（時間排序） |
| 分頁 | cursor：`?limit=50&cursor=<不透明字串>`。回應**一定有** `next_cursor` 這個 key，沒有下一頁就是 `null`。游標是 `created_at + id` 的 base64 |
| 金額 | `cost_usd` 一律是**字串**（`"0.0182"`），不是數字。前端只顯示、不計算 |
| 多餘欄位 | request 帶了沒定義的欄位 → `422 VALIDATION_ERROR`，不會默默忽略 |
| OpenAPI | `/openapi.json`。Console 路由和 `/internal/*` 不列入 |

### 錯誤格式（全平台只有這一種）

```json
{ "error": { "code": "SANDBOX_TIMEOUT", "message": "給人看的說明", "run_id": "0192…", "retryable": true, "details": {} } }
```

- `code`：給程式判斷用，枚舉在 `apps/api/enums.py::ErrorCode`，**不得自創**。
- `retryable`：重試有沒有機會成功。
- `details`：額外資訊，例如哪個欄位錯了。

### 錯誤 code 一覽

| code | HTTP | 可重試 | 什麼時候出現 |
|---|---|---|---|
| `UNAUTHENTICATED` | 401 | 否 | token / key 無效、過期或已撤銷 |
| `PERMISSION_DENIED` | 403 | 否 | 權限不對，例如 A 的 token 去打 B 的 Run、拿別的 Agent 的 key |
| `NOT_FOUND` | 404 | 否 | 資源不存在 |
| `VALIDATION_ERROR` | 422 | 否 | 請求格式錯，`details` 會指出是哪個欄位 |
| `QUOTA_EXCEEDED` | 429 | 是 | 一次性沙盒同時數量滿了（預設 10） |
| `AGENT_NOT_PUBLISHED` | 409 | 否 | 這個 Agent 沒有可用的 `PUBLISHED` 版本 |
| `AGENT_NOT_RUNNING` | 409 | 否 | 這個版本的常駐容器在 10 分鐘內死了 4 次、被平台放棄，要人處理（看 [container-orchestration.md](container-orchestration.md) §存活檢查）。**Run 仍會建立**，直接是 `FAILED`，回應帶 `run_id` 方便事後查 |
| `VERSION_CONFLICT` | 409 | 否 | 同版號正在驗證中；刪除仍被引用的機密；出口規則 host 重複；改已撤銷的 MCP key |
| `BUILD_FAILED` | — | 否 | 版本驗證失敗（寫在版本的 `error` 裡） |
| `SANDBOX_TIMEOUT` | — | 是 | Run 逾時；或同步呼叫等超過 60 秒 |
| `SANDBOX_ERROR` | — | 是 | 容器啟動失敗或崩潰 |
| `LLM_ERROR` | — | 是 | Gateway 打模型供應商失敗 |
| `EGRESS_BLOCKED` | 403 | 否 | 對外請求被平台政策擋下，原因在 `details.reason` |
| `EGRESS_FAILED` | 502 | 是 | 政策放行了，但對方打不通 |
| `USER_CODE_ERROR` | — | 否 | Agent 的程式碼拋出例外 |
| `INTERNAL_ERROR` | 500 | 是 | 沒預期到的錯誤 |

HTTP 欄是 `—` 的，只會出現在 Run 或版本的 `error` 物件裡，不是 HTTP 回應。

---

## Runs（一次呼叫）

### 建立 Run：`POST /v1/agents/{agent_id}/runs` → `202`

```json
{ "entrypoint": "default", "input": { "question": "…" }, "version": "1.2.0",
  "config": { "timeout_seconds": 900, "metadata": {} } }
```

| 欄位 | 必填 | 說明 |
|---|---|---|
| `entrypoint` | 否 | 要呼叫哪個入口，預設 `default` |
| `input` | 是 | 輸入資料，會用該 entrypoint 的 `input_schema` 驗證 |
| `version` | 否 | 省略 = 最新的 `PUBLISHED`（semver 最大）。指定版號時，用該版號**現役**的那一筆 |
| `config.timeout_seconds` | 否 | 預設 900，**上限 900** |

回應（只是「收到了」，不含結果）：

```json
{ "run_id": "…", "agent_id": "…", "agent_version": "1.2.0", "status": "QUEUED",
  "created_at": "…", "events_url": "/v1/runs/…/events" }
```

`trigger`（這筆 Run 是從哪個入口來的）由伺服器決定：REST 和 Console 都是 `API`，MCP 是 `MCP`。body 或任何 header 都改不了。

### 同步呼叫：`POST /v1/agents/{agent_id}/runs:invoke` → `200`

請求格式同上。不同的是：**等到 Run 結束才回**，最多等 **60 秒**。回應是完整的 Run 物件（同下一節）。

超過 60 秒會回 `SANDBOX_TIMEOUT`，但 **Run 本身繼續在跑**，用 `GET /v1/runs/{run_id}` 查。

兩個端點在版本被放棄時都回 `409 AGENT_NOT_RUNNING`，`run_id` 是那筆直接 `FAILED` 的 Run（`error` 同一份）。

### 查 Run：`GET /v1/runs/{run_id}`

```json
{ "run_id": "…", "agent_id": "…", "agent_version": "1.2.0", "status": "SUCCEEDED", "trigger": "API",
  "input": {}, "output": {}, "error": null,
  "usage": { "input_tokens": 1520, "output_tokens": 340, "llm_calls": 3, "duration_ms": 8420, "cost_usd": "0.0182" },
  "created_at": "…", "started_at": "…", "finished_at": "…" }
```

- `status` 有六種：`QUEUED`（排隊）· `SCHEDULING`（配置中）· `RUNNING`（執行中）· `SUCCEEDED` · `FAILED` · `CANCELLED`。狀態怎麼轉換看 [dispatch.md](dispatch.md)。
  在常駐容器佇列裡排隊等的是 `SCHEDULING`；`started_at` 是真的開始執行的時間。
  **沒有 `WAITING_INPUT`**，不要寫、不要斷言。
- `output` / `error` / `usage` / `started_at` / `finished_at` 還沒有值時是 `null`，**但 key 一定存在**。
- 沒打 LLM 的 Run：`usage` 的計數是 `0`、`cost_usd` 是 `"0"`。`usage` 是平台從 `llm_calls` 加總的，**不採信容器自己回報的數字**。
- `agent_version` 是 semver。同版號可能被取代過；要知道實際跑的是哪個 image，去看版本紀錄的 digest。

失敗的 Run 長這樣：

```json
{ "status": "FAILED", "output": null,
  "error": { "code": "USER_CODE_ERROR", "message": "division by zero", "retryable": false, "details": {} } }
```

### 即時事件（SSE）：`GET /v1/runs/{run_id}/events`

用來即時看 Run 跑到哪。斷線可以用 `Last-Event-ID` 續傳，`id` 就是資料庫 `run_events.seq`。

```
id: 42
event: node.start
data: {"node":"llm_node","ts":"…"}
```

事件型別：`run.status` · `node.start` · `node.end` · `token` · `log` · `custom` · `error`。

- `run.status` **只有平台會產生**。收到代表結束的 `run.status` 後，串流就關閉。
- 前端要做到：用 `id` 去重；離開頁面要關掉連線（`EventSource` 重連時自己會帶 `Last-Event-ID`）。

### 列出 Runs：`GET /v1/runs`

Query：`agent_id` · `api_key_id` · `status` · `trigger` · `created_after` · `created_before` · `limit` · `cursor`。
每一列是精簡版（沒有 input / output），並包含 `agent_id` **和 `agent_name`**。

例：看最近失敗、從 MCP 來的 Run：`GET /v1/runs?status=FAILED&trigger=MCP`

### ⚠️ 取消 Run：`POST /v1/runs/{run_id}/cancel` —— 沒實作

Console 的 `api.js` 有呼叫它，但**後端沒有這條路由**（會 `404`）。
要做的話規則是：只在 `QUEUED` / `SCHEDULING` / `RUNNING` 有效，其他狀態回 `409`；轉成 `CANCELLED`，並銷毀沙盒／從常駐容器撤回。

---

## Agents

| Method | Path | 回應 | 說明 |
|---|---|---|---|
| `POST` | `/v1/agents` | `201` | `{"name", "description"}`。`name` 全平台唯一，重複 → `422` |
| `GET` | `/v1/agents` | `200` | 列表：`agent_id` · `name` · `description` · `latest_published_version` · `updated_at` |
| `GET` | `/v1/agents/{id}` | `200` | 上面那些 + `created_at` + `endpoints` |
| `PATCH` | `/v1/agents/{id}` | `200` | 只能改 `name` / `description` |
| `DELETE` | `/v1/agents/{id}` | `204` | **硬刪，不可復原**（見 [agent-registry.md](agent-registry.md) §刪除） |
| `GET` | `/v1/agents/{id}/openapi.json` | `200` | 這個 Agent 專屬的 OpenAPI 3.1，由 entrypoints 生成（P3） |

`name` 規則：只能用英數字、`.`、`_`、`-`，且第一個字元要是英數字，最長 255。

詳情裡的 `endpoints`：

```json
"endpoints": { "rest": "http://…/v1/agents/{id}/runs", "openapi": "http://…/v1/agents/{id}/openapi.json" }
```

**沒有每個 Agent 自己的 MCP 網址**：MCP 是平台的單一接口，看 [mcp.md](mcp.md)。

⚠️ 已知缺陷（BOARD ZEN-016）：`GET /v1/agents` 最後一頁缺 `next_cursor`；`GET /v1/agents/{id}` 的值是 null 時，會少 `description` / `latest_published_version` 這兩個 key（`response_model_exclude_none` 造成）。約定是 key 一定要在。

---

## 版本

| Method | Path | 回應 | 說明 |
|---|---|---|---|
| `POST` | `/v1/agents/{id}/versions` | `202` | `{"semver", "image_ref"}`：註冊，並在背景驗證 |
| `GET` | `/v1/agents/{id}/versions` | `200` | 列表（含 `manifest` / `entrypoints`）。被取代的不會出現 |
| `GET` | `/v1/agents/{id}/versions/{semver}` | `200` | 這個版號**最近一次註冊**的那筆（註冊後用它輪詢結果） |

註冊範例：

```bash
curl -s -X POST localhost:8000/v1/agents/$AID/versions \
  -H 'content-type: application/json' \
  -d '{"semver":"1.2.0","image_ref":"localhost:5000/acme/a@sha256:0123…（64 個 hex）"}'
```

回應：

```json
{ "agent_version_id": "…", "semver": "1.2.0", "version_id": "0123456789ab", "source_type": "OCI_IMAGE",
  "artifact": { "image_ref": "ghcr.io/acme/a@sha256:<64hex>", "digest": "sha256:<64hex>",
                "platform": null, "size_bytes": null, "runtime_contract": null },
  "status": "BUILDING", "error": null, "created_at": "…" }
```

規則：
- `image_ref` 只接受 `repository@sha256:<64 個 hex>`。**只有 tag**（如 `:latest`）、multipart、任何多餘欄位 → `422`。
- `status`：`BUILDING`（驗證中）· `PUBLISHED`（可用）· `FAILED`。（`SUPERSEDED` 只存在資料庫，任何回應都看不到。）
- `version_id`、`artifact.image_ref`、`artifact.digest` 任何狀態都有值。`platform` / `size_bytes` / `runtime_contract` / `manifest` / `entrypoints` 在 `BUILDING` 時是 `null`，`PUBLISHED` 一定有值。
- 失敗時：`error.code = BUILD_FAILED`，`error.details.stage` 是 `PULL`（拉不到）/ `INSPECT`（檢查 image）/ `VALIDATE`（驗證 Agent 內容）之一。
- **同版號再送一次 = 取代**。如果同版號已經在 `BUILDING` → `409`；`image_ref` 和現役的一樣 → `202` 直接回現役那筆。完整規則看 [agent-registry.md](agent-registry.md)。

---

## 常駐容器

| Method | Path | 回應 | 說明 |
|---|---|---|---|
| `GET` | `/v1/agents/{id}/containers` | `200` | 這個 Agent 的容器列表 |
| `POST` | `/v1/agents/{id}/containers` | `202` | 預熱（先開好）。body 可省略，或 `{"version": "1.2.0"}`；省略就用最新 `PUBLISHED` |
| `POST` | `/v1/agents/{id}/containers/{cid}/restart` | `202` | 重啟，`restart_count` 加 1 |
| `DELETE` | `/v1/agents/{id}/containers/{cid}` | `202` | 停止 |

```json
{ "container_id": "…", "agent_id": "…", "agent_version": "1.2.0", "status": "READY",
  "inflight_runs": 3, "max_concurrent_runs": 50, "restart_count": 0,
  "started_at": "…", "last_health_at": "…", "error": null }
```

- `status`：`STARTING`（啟動中）· `READY`（可接工作）· `UNHEALTHY`（健康檢查失敗）· `STOPPED` · `FAILED`。
- `inflight_runs / max_concurrent_runs` = 現在同時在跑幾筆 / 最多能跑幾筆。
- 寫入類的端點（預熱、重啟、停止）只是**登記「想要的狀態」**，實際動作由 worker 做，所以回 `202`。看 [container-orchestration.md](container-orchestration.md)。

---

## API key（給下游應用呼叫 REST 用）

一個 Agent 可以發很多把 key 給不同的下游應用。

| Method | Path | 回應 |
|---|---|---|
| `POST` | `/v1/agents/{id}/api-keys`，body `{"name"}`（1–128 字） | `201`，**`key` 明文只在這次出現** |
| `GET` | `/v1/agents/{id}/api-keys` | `200`，**含已撤銷的**，永遠不含 key 本身 |
| `DELETE` | `/v1/agents/{id}/api-keys/{key_id}` | `200`，帶 `revoked_at`（軟撤銷，重複撤銷沒關係） |

```json
{ "key_id": "…", "agent_id": "…", "name": "行銷站", "key": "zk_live_…", "key_prefix": "zk_live_a1b2",
  "created_at": "…", "last_used_at": null, "revoked_at": null }
```

使用方式（只用於 REST 建 Run）：

```bash
curl -X POST localhost:8000/v1/agents/$AID/runs \
  -H "authorization: Bearer zk_live_…" -H 'content-type: application/json' \
  -d '{"input":{"question":"hi"}}'
```

注意：**預設沒帶 key 也能呼叫**，所以 key 的作用是「記帳：這筆是誰打的」，不是「擋人」。完整規則看 [agent-registry.md](agent-registry.md) §下游 API key。

---

## MCP key（給外部 AI 平台連 `/mcp` 用）

Console 的 `/console/mcp` 頁就是用這組端點。一把 key 勾選一組 Agent；對端拿它連 `/mcp`（看 [mcp.md](mcp.md)）。

| Method | Path | 回應 |
|---|---|---|
| `POST` | `/v1/mcp-keys`，body `{"name"（1–128 字）, "agent_ids": [...]}` | `201`，**`key` 明文只在這次出現**。`agent_ids` 可以是空的；有不存在的 Agent → `404` |
| `GET` | `/v1/mcp-keys` | `200`，`{"endpoint", "items"}`。**含已撤銷的**，永遠不含 key 本身 |
| `PUT` | `/v1/mcp-keys/{key_id}/agents`，body `{"agent_ids": [...]}` | `200`，**整份取代**勾選。已撤銷的 key → `409` |
| `DELETE` | `/v1/mcp-keys/{key_id}` | `200`，帶 `revoked_at`（軟撤銷，重複撤銷沒關係） |

發 key 的範例：

```bash
curl -s -X POST localhost:8000/v1/mcp-keys -H 'content-type: application/json' \
  -d '{"name":"Dify 正式環境","agent_ids":["01a1…"]}'
```

```json
{ "key_id": "…", "name": "Dify 正式環境", "key": "zmcp_live_…", "key_prefix": "zmcp_live_a1b2",
  "agent_ids": ["01a1…"], "created_at": "…", "last_used_at": null, "revoked_at": null }
```

- `endpoint` 是 `<base_url>/mcp`，Console 顯示給你複製用。
- `agent_ids` 只列「還存在」的 Agent。
- 改勾選要**傳完整清單**（不是增減）：想取消勾選 B，就送「原本清單去掉 B」。

---

## 用量與除錯

| Method | Path | 說明 |
|---|---|---|
| `GET` | `/v1/agents/{id}/usage` | `totals`（總計）· `by_key`（依 key）· `by_model`（依模型） |
| `GET` | `/v1/agents/{id}/llm-calls` | 逐筆 LLM 呼叫，可翻頁 |

共用的 query：`api_key_id` · `since` · `until` · `model` · `credential_source`。`llm-calls` 另有 `status_code` · `errors_only` · `limit` · `cursor`。

- `by_key` **一定有 `key_id: null` 那一列**：沒帶 key 的呼叫（包含 Console 發起的）都算在這。
- 用量是靠 `runs.api_key_id` join 回來的，`llm_calls` 自己沒有 key 欄位。
- `errors_only=true` 等於 `error IS NOT NULL OR status_code >= 400`。

一筆 `llm-calls` 長這樣（這筆是被供應商限流）：

```json
{ "call_id": "…", "run_id": "…", "run_status": "SUCCEEDED", "api_key_id": "…", "model": "gpt-4o",
  "input_tokens": 10, "output_tokens": 0, "cost_usd": "0", "latency_ms": 120, "status_code": 429,
  "error": "rate limited", "credential_source": "AGENT", "created_at": "…" }
```

---

## 每個 Agent 的憑證、設定、機密、對外白名單

這四種**都綁 Agent、不綁版本**：發新版自動沿用；改了之後，所有版本的**下一筆 Run** 立刻生效。行為細節看 [gateway.md](gateway.md)。

| 種類 | 能讀回來嗎 | 會進容器嗎 |
|---|---|---|
| LLM 憑證 | 只看得到尾四碼 | 不會（由 Gateway 代打） |
| 設定 `config` | **明文** | 會，每筆 Run 進 `ctx.config` |
| 機密 `secrets` | 只看得到尾四碼 | **永遠不會**，容器只拿到「名字清單」 |
| 對外白名單、寄件者 | 可以（不含密碼） | 規則不進容器 |

### LLM 憑證

這個 Agent 要用自己的 LLM 金鑰時才設；沒設就用平台預設金鑰。

| Method | Path | 說明 |
|---|---|---|
| `PUT` | `/v1/agents/{id}/llm-credential` | `{"api_key"（8–512 字）, "base_url"?, "default_model"?}` |
| `GET` | `/v1/agents/{id}/llm-credential` | `{"configured", "last_four", "base_url", "default_model", "updated_at"}`。沒設定時 `configured:false`，其餘 `null` |
| `DELETE` | `/v1/agents/{id}/llm-credential` | `204`，退回平台預設金鑰 |

**沒有任何端點能拿回金鑰明文或密文**（這是約定禁止的，不是還沒做）。
沒設 `ZENTRA_SECRET_KEY` 時 `PUT` 回 `422`，不會退而求其次明文儲存。

### 設定（config）

**用途：** 給 Agent 程式的「參數」。同一個 image 不用重新打包發版，就能換行為——例如把搜尋輪數從 3 改成 5，下一筆 Run 就生效。
Agent 在程式裡用 `ctx.config["MAX_ROUNDS"]` 讀。

`GET` / `PUT /v1/agents/{id}/config`。body 和回應都是 `{"config": {...}, "updated_at"}`。

```json
{ "config": { "MAX_ROUNDS": 3, "LANG": "zh-TW" } }
```

- 只有**整份取代**，沒有 PATCH。沒設過：`config: {}`、`updated_at: null`。
- 鍵名規則 `^[A-Za-z_][A-Za-z0-9_]{0,63}$`；最多 100 個頂層鍵；值可以是任何 JSON；整份 ≤ 64 KiB。
- **設定不是機密**：明文回傳、而且完整進到容器。密碼、token 請放「機密」。

### 機密（secrets）

**用途：** Agent 去連「別的服務」要用的密碼或 key（例如交易所 API key、SMTP 密碼）。**打 LLM 用的金鑰不放這裡**，那是「LLM 憑證」。
機密不會單獨起作用，一定要被引用才有用：出口規則的 `auth_secret_name`（平台代打外部 API 時把它塞進 header），或寄件者的 `password_secret_name`。Agent 程式本身讀不到值。

| Method | Path | 說明 |
|---|---|---|
| `GET` | `/v1/agents/{id}/secrets` | `{"items": [{"name", "last_four", "created_at", "updated_at"}]}` |
| `PUT` | `/v1/agents/{id}/secrets/{name}` | `{"value"}`（1–4096 字）。新建和覆寫都回 `200` |
| `DELETE` | `/v1/agents/{id}/secrets/{name}` | `204`，硬刪，重複刪沒關係。**還被出口規則或寄件者引用** → `409`，`details.referenced_by` 會列出是誰在用 |

- 名字規則同設定鍵名；每個 Agent 最多 50 個。
- 用 Fernet 加密儲存（因為之後要解回來用）。沒設 `ZENTRA_SECRET_KEY` → `422`。
- `last_four`：長度 ≥ 4 是最後四個字；< 4 則是 `*` 重複該長度次。

### 對外白名單（egress rules）

容器想對外打 HTTP，只有白名單上有的才放行（細節看 [gateway.md](gateway.md)）。

| Method | Path | 回應 |
|---|---|---|
| `GET` | `/v1/agents/{id}/egress-rules` | `200` |
| `POST` | `/v1/agents/{id}/egress-rules` | `201` |
| `PUT` | `/v1/agents/{id}/egress-rules/{rule_id}` | `200`，整條取代 |
| `DELETE` | `/v1/agents/{id}/egress-rules/{rule_id}` | `204` |

範例：只允許 `GET https://api.binance.com/api/v3/…`：

```json
{ "host": "api.binance.com", "methods": ["GET"], "path_prefix": "/api/v3/",
  "auth_secret_name": null, "auth_header": null, "description": "Binance 現貨行情" }
```

違反以下任何一條都是 `422`：
- `host` 是小寫主機名（可以沒有點，例如 `localhost`）或 IP（IPv4 / IPv6，存成標準寫法）。不能有 `*`、scheme（`https://`）、port、路徑。一條規則放行那個主機的所有 port。
- `methods` 只能是 `GET` / `POST`，且不能是空的。
- `auth_secret_name` 和 `auth_header` 要**成對**出現，而且那個機密要存在（平台會替容器把機密塞進這個 header，容器自己看不到機密）。
- `auth_header` 不能是 `Accept` / `Content-Type` / `Host` / `Content-Length`。
- 每個 Agent 最多 20 條。

同一個 Agent 內 `host` 重複 → `409`。

### 寄件者（email sender）

`GET` / `PUT` / `DELETE /v1/agents/{id}/email-sender`

```json
{ "smtp_host": "smtp.gmail.com", "smtp_port": 587, "username": "me@gmail.com", "from_address": "me@gmail.com",
  "password_secret_name": "SMTP_PASSWORD", "allowed_recipients": ["me@gmail.com"] }
```

- 回應多了 `configured` 和 `updated_at`。
- **沒有密碼欄位**：body 帶 `password` → `422`。密碼只能用「機密的名字」指定（上例的 `SMTP_PASSWORD` 要先在機密裡存好）。
- `allowed_recipients` 必填，1–10 個**完整地址**（不能用萬用字元）。
- `smtp_port` 只能是 `587` / `465` / `25`，一律要求 TLS。

### 對外請求稽核

`GET /v1/agents/{id}/egress-calls`

Query：`kind`（`HTTP` 或 `EMAIL`）· `run_id` · `since` · `until` · `host` · `status_code` · `errors_only` · `limit` · `cursor`。

```json
{ "call_id": "…", "run_id": "…", "kind": "HTTP", "rule_id": "…", "method": "GET",
  "host": "api.binance.com", "path": "/api/v3/klines", "status_code": 200,
  "request_bytes": 0, "response_bytes": 18422, "latency_ms": 310, "error": null, "created_at": "…" }
```

- `path` 不含 query string。
- `kind=EMAIL` 時：`path` 放收件人、`host` 放 SMTP 主機。
- `error` 的形狀看 [gateway.md](gateway.md) §稽核。

---

## 沒有實作的（不要假設存在）

- `POST /v1/runs/{id}/cancel`（見上）
- `Idempotency-Key` header（重送保證）
- `/v1` 的平台層認證（session / api_key）
