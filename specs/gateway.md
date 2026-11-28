# Internal Gateway（`/internal/*`）

`app` 上給容器用的路由，也是容器唯一連得到的對端。長期憑證（LLM 金鑰、機密、SMTP 密碼）只留在這一側；容器只拿短命 token。
不列入公開 OpenAPI，前端不呼叫。`/internal/*` 在主機 `:8000` 也連得到，所以每條路由都必須驗 token。

```
容器（沒有金鑰）                 Gateway（app）
  ctx.llm()        ──run token──▶ 換上真金鑰 → 打 LLM 供應商
  ctx.http         ──run token──▶ 查白名單 → 代打外部 HTTP
  ctx.send_email() ──run token──▶ 查收件人 → SMTP 寄出
```

## Token

| token | 發給 | 綁定 | 有效期 | 能打 |
|---|---|---|---|---|
| run token | 一筆 Run | `run_id` | = `timeout_seconds` | `/runs/{自己}/*`、`/llm/*`、`/egress/*`、`/notify/*` |
| container token | 常駐容器 | `agent_id + container_uuid` | 30 天 | 只有 `GET /agents/{自己}/work` |
| validation token | 驗證容器 | `agent_version_id` | ≤ 600 秒 | 只有 `POST /versions/{自己}/validation`，且版本仍是 `BUILDING` |

- 都是 `secrets.token_urlsafe(32)`，存 Redis，`Authorization: Bearer`。
- 打別人的東西 → `403`；無效或已撤銷 → `401`。
- run token 在 Run 進終態時撤銷。常駐容器的 run token 在容器取走工作時才發。

## 路由

| Method | Path | token | 做什麼 |
|---|---|---|---|
| `GET` | `/internal/runs/{run_id}/bootstrap` | run | 一次性沙盒取 Run 資料 |
| `POST` | `/internal/runs/{run_id}/events` | run | 回寫事件陣列 |
| `POST` | `/internal/runs/{run_id}/result` | run | 存 output / error / usage，不轉狀態 |
| `POST` | `/internal/llm/v1/{chat/completions \| responses}` | run | 代打 LLM |
| `POST` | `/internal/egress/request` | run | 代打外部 HTTP |
| `POST` | `/internal/notify/email` | run | 寄信 |
| `GET` | `/internal/agents/{agent_id}/work` | container | 長輪詢取工作（25 秒；`200` / `204`） |
| `POST` | `/internal/versions/{version_id}/validation` | validation | 回報驗證結果 |
| `POST` | `/internal/registry/events` | 無 | registry push 通知 → 註冊版本。內容不被信任，註冊前回頭向 registry 確認 tag 指向該 digest。永遠回 200 |

### 每筆 Run 帶進容器的資料

一次性沙盒從 bootstrap 拿，常駐容器從工作項拿（兩條路都要改）：

```json
{ "run_id": "…", "entrypoint": "default", "input": {}, "manifest": {},
  "default_model": "gpt-4o-mini", "config": {}, "secret_names": ["SMTP_PASSWORD"] }
```

- `default_model`、`config`、`secret_names` 每筆 Run 重新計算。`config` 是頂層欄位，不併進 `manifest`。
- `secret_names` 只有排序後的名字。驗證容器一律 `config = {}`、`secret_names = []`。

### 回寫

- `POST /result`：`{output, error, usage}` → `202 {"accepted": true}`。相同內容重送 OK，不同內容 → `422`。最終狀態由 worker 決定。
- `POST /events`：頂層陣列 `[{event_id, type, payload}]` → `202 {accepted, last_seq}`。`event_id` 重送沿用；容器不能送 `run.status`。
- `POST /validation`：`{valid, runtime_contract, manifest, entrypoints, error}`。平台先驗入口格式（每項恰好四個欄位、名稱不重複、兩個 schema 是合法的 JSON Schema 且最外層是 object），不合格 `422`；通過才保存。
- `POST /result` 的 `output` 平台會用入口的 `output_schema` 再驗，不符合改記成 `USER_CODE_ERROR`（`details.stage = "OUTPUT"`）。容器不一定是 Python SDK，見 `specs/runtime.md`。

## LLM 代打

`ctx.llm()` 回傳 LangChain chat model，`base_url = http://app:8000/internal/llm/v1`、`api_key = run token`。

- 只代理 `chat/completions`、`responses`，其他 `404`。
- 金鑰：Agent 自己的（`agent_llm_credentials`）→ 沒有就用 `ZENTRA_LLM_API_KEY` / `ZENTRA_LLM_BASE_URL`。`llm_calls.credential_source` 記 `AGENT` / `PLATFORM`。
- 轉發前剝掉 `authorization`、`x-api-key`、`openai-organization`、`openai-project`。
- `stream: true` 時強制加 `stream_options.include_usage`。
- 計量：`prompt_tokens` / `completion_tokens` 或 `input_tokens` / `output_tokens`；`cost_usd` 依 `LLM_PRICES` 當下算好存起來，未設價格記 0。
- 供應商錯誤也寫 `llm_calls`，錯誤原樣回容器；平台沒設金鑰 → `LLM_ERROR`。
- `default_model`：Agent 憑證的 → `ZENTRA_LLM_DEFAULT_MODEL`。SDK 不准寫死預設值。
- 逾時 `ZENTRA_LLM_TIMEOUT_SECONDS`（120）。

## 設定 / 機密 / 能力

| 層 | 存哪 | 進容器 |
|---|---|---|
| 設定 | `agent_settings`，明文 | 會，`ctx.config` |
| 機密 | `agent_secrets`，Fernet | 不會，只有名字進 `ctx.secret_names` |
| 能力 | `agent_egress_rules`、`agent_email_sender` | 不會 |

## 外部 HTTP：`POST /internal/egress/request`

```json
{ "url": "https://api.binance.com/api/v3/klines?symbol=BTCUSDT", "method": "GET",
  "headers": { "Accept": "application/json" }, "body": null, "timeout_seconds": 10 }
```

回 `200 {status, headers, body}`；上游 4xx/5xx 也是 `200`，碼在 `status`。

規則：
1. `headers` 只准 `Accept`、`Content-Type`，其他 → `422`（`details.header`）。認證用規則的 `auth_secret_name` + `auth_header` 由平台加上。
2. 固定送 `User-Agent: Zentra-Egress/1`（OKX 會擋 `Python-urllib/*`）。
3. 不跟隨 redirect：3xx → `EGRESS_BLOCKED`（`REDIRECT`，`details.location`）。沒有任何旗標能關掉這條。
4. `http` / `https` 都可以，port 不限；其他 scheme → `SCHEME_NOT_ALLOWED`。
5. 不檢查目的是否為內網，只看白名單。白名單擋平台服務名與 loopback，但只擋名字：平台的內網 IP 或 `host.docker.internal:8000/6000/5432/6379` 仍連得到。
6. 上限：request ≤ 64 KiB、response ≤ 1 MiB、`timeout_seconds` 預設 10 上限 15、每 Run ≤ `EGRESS_MAX_CALLS_PER_RUN`（100）次（被擋的也算，計數來自 `egress_calls`）。超量一律錯誤，不截斷。
7. 回傳 header 只留 `content-type`、`content-length`、`date`、`retry-after`、`x-ratelimit-*`。

## 寄信：`POST /internal/notify/email`

```json
{ "to": ["me@example.com"], "subject": "…（≤ 200 字）", "text": "…", "html": null }
```

成功 `202 {"accepted": true, "message_id": "…"}`。

- 寄件者、帳密、SMTP 主機全由平台決定；request 帶 `from` / 主機 / 密碼 → `422`。
- 任一收件人不在 `allowed_recipients` → 整封不寄（`RECIPIENT_NOT_ALLOWED`）。
- 一律 TLS（587 STARTTLS、465 隱式）。
- 上限：內容 ≤ 256 KiB；每 Run ≤ `EMAIL_MAX_PER_RUN`（5）；每 Agent 滾動 60 分鐘 ≤ `EMAIL_MAX_PER_HOUR`（60）。計嘗試次數。
- 不冪等，不支援附件。

## `EGRESS_BLOCKED` 的 `details.reason`

| reason | 類型 |
|---|---|
| `NO_RULES` · `HOST_NOT_ALLOWED` · `METHOD_NOT_ALLOWED` · `PATH_NOT_ALLOWED` · `SCHEME_NOT_ALLOWED` · `REDIRECT` · `REQUEST_TOO_LARGE` · `RESPONSE_TOO_LARGE` · `RUN_CALL_LIMIT` | HTTP |
| `SENDER_NOT_CONFIGURED` · `RECIPIENT_NOT_ALLOWED`（`details.recipient`）· `BODY_TOO_LARGE` · `RUN_EMAIL_LIMIT` · `HOURLY_EMAIL_LIMIT` · `SMTP_AUTH` · `TLS_REQUIRED` | EMAIL |

打不通（DNS、拒絕連線、逾時）→ `EGRESS_FAILED`（502）。請求寫錯 → `422`，用 `details.header` / `details.field`，沒有 `reason`。

## 稽核 `egress_calls`

通過 token 驗證之後的每條回傳路徑都先寫一列（含被擋與 `422`）。

| 情況 | `error` | `status_code` |
|---|---|---|
| 被擋 | `EGRESS_BLOCKED: <reason>` | `null` |
| 請求寫錯 | `VALIDATION_ERROR: <細節>` | `null` |
| 打不通 | `EGRESS_FAILED: <細節>` | `null` |
| 送出（含上游 4xx/5xx） | `null` | 上游碼 |

`path` 不存 query string。寄信：`kind = EMAIL`、`host` = SMTP 主機、`path` = 收件人。
