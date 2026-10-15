# Internal Gateway 系統設計

> `app` 上的 `/internal/*`：**沙盒唯一的網路對端**，P4 的實作。
> 長期憑證（LLM 金鑰、機密、SMTP 密碼）全部留在這一側，沙盒只拿短命 token 來換服務。
> 前端永遠不呼叫這些路由；它們不列入公開 OpenAPI。

## 為什麼存在

沙盒跑的是使用者程式碼。金鑰一旦進去，「程式碼被攻陷」就等於「金鑰洩漏」，而且平台不知道它被拿去做什麼。
金鑰留在 Gateway 這一側，最壞情況是某個 Run 多花了錢 —— **而那筆錢有紀錄**。

沙盒網段沒有對外路由，所以沙盒要做的每一件對外的事（打 LLM、打 HTTP、寄信、回報事件）都只能經過這裡。

## 三種 token

| token | 發給誰 | 綁定 | TTL | 能打哪些路由 |
|---|---|---|---|---|
| **run token** | 一筆 Run（一次性沙盒放 env；常駐容器跟著工作項） | 一個 `run_id` | = 該 Run 的 `timeout_seconds` | `/runs/{自己}/*`、`/llm/*`、`/egress/*`、`/notify/*` |
| **container token** | 一個常駐容器 | `agent_id + container_uuid` | 30 天 | **只有** `GET /agents/{自己的 agent}/work` |
| **validation token** | 一個驗證容器 | 一個 `agent_version_id` | ≤ 600 秒 | **只有** `POST /versions/{自己}/validation`，且版本必須還在 BUILDING |

- 全部是 `secrets.token_urlsafe(32)`，存 Redis（見 [redis.md](redis.md)），`Authorization: Bearer <token>`。
- **帶 A 的 token 打 B → `403 PERMISSION_DENIED`**；無效或已撤銷 → `401`。
- run token 在 Run 進終態時撤銷（一次性沙盒在 finally，常駐路徑在 finalize）；之後同一把一律 `401`。
- `/internal/*` 跟 `/v1/*` 掛在同一個 app，主機的 `:8000` 也連得到 —— **「internal」不代表有網段封鎖，每一條都必須驗 token**。

## 路由

| Method | Path | token | 做什麼 |
|---|---|---|---|
| `GET` | `/internal/runs/{run_id}/bootstrap` | run | 一次性沙盒取這筆 Run 的資料 |
| `POST` | `/internal/runs/{run_id}/events` | run | 回寫事件（陣列），見 [dispatch.md](dispatch.md) §事件 |
| `POST` | `/internal/runs/{run_id}/result` | run | 回寫 output / error / usage，**只保存不轉狀態** |
| `POST` | `/internal/llm/v1/{chat/completions \| responses}` | run | OpenAI 相容的 LLM 代打 |
| `POST` | `/internal/egress/request` | run | 對外 HTTP 代打 |
| `POST` | `/internal/notify/email` | run | 寄信 |
| `GET` | `/internal/agents/{agent_id}/work` | container | 常駐容器 long-poll 取工作（25 秒，`200` 工作項 / `204` 沒工作） |
| `POST` | `/internal/versions/{version_id}/validation` | validation | 回報驗證結果與 entrypoints |

### 每筆 Run 帶進沙盒的資料

一次性沙盒從 bootstrap 拿，常駐容器從工作項拿 —— **兩條路都要帶，只改一條等於沒改**。

```json
{ "run_id": "…", "entrypoint": "default", "input": {}, "manifest": {},
  "default_model": "gpt-4o-mini",
  "config": { "symbols": ["BTCUSDT"] },
  "secret_names": ["SMTP_PASSWORD"] }
```

常駐容器的工作項另外帶 `run_token`：app 在容器取走工作的那一刻才發，TTL 從那時算（排隊時間不吃掉它）。

- `default_model`、`config`、`secret_names` **每筆 Run 重算**：中途改設定，下一筆就生效；已經在跑的不受影響。
- `config` 是頂層欄位，**不得併進 `manifest`**（manifest 是版本的不可變內容）。
- `secret_names` 只有排序後的名字：沒有值、長度、尾四碼。
- 驗證容器一律 `config = {}`、`secret_names = []`，而且沒有任何路由給它取設定。

### 結果與事件

- `POST /result`：`{"output": {}, "error": null, "usage": {}}`，回 `202 {"accepted": true}`。第一次寫入記 `result_received_at`；
  相同內容重送冪等，不同內容 → `422`。常駐路徑收到結果後送 `zentra.run.finalize`，終態由 worker 決定。
- `POST /events`：body 是頂層陣列 `[{"event_id", "type", "payload"}]`，回 `202 {"accepted": N, "last_seq": 42}`。
  沙盒不能送 `run.status`。
- `POST /validation`：`{"valid", "runtime_contract", "manifest", "entrypoints", "error"}`；只保存，不轉 PUBLISHED。

## LLM 代打

```
沙盒 ctx.llm()（一個 base_url = http://app:8000/internal/llm/v1、api_key = run token 的標準 LangChain chat model）
  ──▶ Gateway：驗 run token → 選金鑰 → 換上真金鑰 → 打供應商 → 計量 → 寫 llm_calls → 原樣回傳
```

- **路徑白名單**：只代理 `chat/completions` 與 `responses`，其他回 `404`。這個代理握著真金鑰，能打什麼由平台決定。
- **金鑰選擇**：該 Agent 自己的憑證（`agent_llm_credentials`，Fernet 加密）→ 平台預設（`ZENTRA_LLM_API_KEY` / `ZENTRA_LLM_BASE_URL`）。
  每一列 `llm_calls` 記 `credential_source`（`AGENT` | `PLATFORM`）—— 憑證可變而版本不可變，要留下「那一次用了誰的」。
- **轉發前剝掉** `authorization`、`x-api-key`、`openai-organization`、`openai-project` 等 header：沙盒送來的是 run token，轉出去等於拿內部憑證問外部服務。
- **串流**：`chat/completions` 的 `stream: true` 會強制加 `stream_options.include_usage`，才計得到 token。
- **計量**：`chat/completions` 讀 `prompt_tokens` / `completion_tokens`，`responses` 讀 `input_tokens` / `output_tokens`。
  `cost_usd` 依 `LLM_PRICES` 在**呼叫當下**算好存下，之後改價不回溯；沒設定價格的 model 記 0。
- **失敗也記**：供應商錯誤照樣寫 `llm_calls`（`status_code`、`error`），錯誤 body 原樣回給沙盒。平台沒設金鑰 → `LLM_ERROR`。
- `default_model` 由平台算好送進沙盒（Agent 憑證的 → 平台預設 `ZENTRA_LLM_DEFAULT_MODEL`）。**SDK 不准自己補寫死的值**，否則 Console 設的預設 model 永遠到不了。
- 逾時 `ZENTRA_LLM_TIMEOUT_SECONDS`（預設 120）。

## 設定、機密、出口：三層

| 層 | 存哪 | 進得了沙盒嗎 |
|---|---|---|
| 設定 | `agent_settings`，明文 JSON | **會**，每筆 Run 進 `ctx.config` |
| 機密 | `agent_secrets`，Fernet 加密（要解得回來才能登入 SMTP / 打外部 API） | **永遠不會**，只有名字進 `ctx.secret_names` |
| 能力 | `agent_egress_rules`、`agent_email_sender` | 規則不進沙盒；沙盒只看得到請求成不成功 |

三層都綁 Agent 不綁版本；沒設 `ZENTRA_SECRET_KEY` 時拒絕存機密（不退回明文）。端點見 [api.md](api.md)。

> ⚠️ **通用出口 proxy 是一條真正的外洩通道。** 白名單擋得住「送到哪」，擋不住「送了什麼」—— GET 的 query string 就是資料。
> 大小與次數上限只是壓低頻寬。這個取捨成立的前提是「只跑自己信得過的程式碼」。

## 對外 HTTP 代打：`POST /internal/egress/request`

```json
{ "url": "https://api.binance.com/api/v3/klines?symbol=BTCUSDT", "method": "GET",
  "headers": { "Accept": "application/json" }, "body": null, "timeout_seconds": 10 }
```

```json
{ "status": 200, "headers": { "content-type": "application/json" }, "body": "…" }
```

**硬規則**

1. **信封語意**：只要請求真的送出去了，Gateway 回 `200`，上游狀態碼放 `status`。上游 4xx/5xx 不是平台錯誤 —— agent 要看得到狀態碼才寫得出「主源失敗換備援」。
2. **沙盒不得自帶認證**：`headers` 只准 `Accept` 與 `Content-Type`，其他 → `422`，`details.header` 指名（不是 `EGRESS_BLOCKED`）。
   要認證就在規則上設 `auth_secret_name` + `auth_header`，平台解密後加上去。平台固定送 `User-Agent: Zentra-Egress/1`
   （**改之前要重驗交易所**：OKX 會擋 `Python-urllib/*`，改掉的症狀是備援源全掛）。
3. **不跟隨 redirect**：3xx → `EGRESS_BLOCKED`（`REDIRECT`，`details.location`）。跟隨等於讓對方一行 302 繞過白名單。
4. **只准 `https`**。
5. **解析結果是 private / loopback / link-local / metadata 位址一律擋**（含 `169.254.169.254`）。proxy 從 `app` 發出，而 `app` 也在 `zentra-net` 上。
   **而且只連到檢查過的那個 IP**（擋 DNS rebinding）。
6. **沒有任何旗標或環境變數可以關掉第 3、4、5 點**，連預設關閉的都不准存在 —— 存在的旁路遲早會被打開，而打開時沒有症狀。
7. **上限**（環境變數可調）：request body ≤ 64 KiB、response ≤ 1 MiB、`timeout_seconds` 預設 10 上限 15、每筆 Run ≤ `EGRESS_MAX_CALLS_PER_RUN`（100）次。
   **超量不截斷、不夾值，直接當錯誤**：`timeout_seconds > 15` 是 `422`。次數計的是「通過認證的嘗試」，**被擋的也算**（失控的迴圈通常正是一直失敗的那個）；計數來源就是 `egress_calls`，不另設計數器。
8. 回傳的 header 只有白名單子集：`content-type`、`content-length`、`date`、`retry-after`、`x-ratelimit-*`。

## 寄信：`POST /internal/notify/email`

```json
{ "to": ["me@example.com"], "subject": "…（≤ 200 字）", "text": "…", "html": null }
```
→ `202 {"accepted": true, "message_id": "<…@zentra>"}`

1. 寄件者、帳號、密碼、SMTP 主機都由平台決定（`agent_email_sender` + 機密）；request 帶 `from`、主機或密碼 → `422`。
2. 收件人逐一比對 `allowed_recipients`，**有一個不在就整封不寄**（`RECIPIENT_NOT_ALLOWED`）。
3. 一律 TLS（587 STARTTLS、465 隱式 TLS），升不上去 → `TLS_REQUIRED`。
4. 上限：內容 ≤ 256 KiB；每筆 Run ≤ `EMAIL_MAX_PER_RUN`（5）；每個 Agent **滾動 60 分鐘** ≤ `EMAIL_MAX_PER_HOUR`（60）。都計嘗試，含認證失敗（反覆認證失敗正是 Gmail 鎖帳號的主因）。
5. `202` 表示 SMTP 伺服器收下了，不表示對方讀到。**不冪等**，重送就是再寄一封。不附檔、不排程、不處理退信。
6. 沒設寄件者 → `EGRESS_BLOCKED`（`SENDER_NOT_CONFIGURED`），不是靜默不寄。

## `EGRESS_BLOCKED` 的 `details.reason`

| reason | 出現在 | 意思 |
|---|---|---|
| `NO_RULES` | HTTP | 一條白名單都沒有 |
| `HOST_NOT_ALLOWED` / `METHOD_NOT_ALLOWED` / `PATH_NOT_ALLOWED` | HTTP | 不符合規則 |
| `SCHEME_NOT_ALLOWED` | HTTP | 不是 https |
| `PRIVATE_ADDRESS` | HTTP | 解析到內網 |
| `REDIRECT` | HTTP | 上游回 3xx |
| `REQUEST_TOO_LARGE` / `RESPONSE_TOO_LARGE` | HTTP | 超過大小上限 |
| `RUN_CALL_LIMIT` | HTTP | 超過每 Run 次數 |
| `SENDER_NOT_CONFIGURED` | EMAIL | 沒設寄件者 |
| `RECIPIENT_NOT_ALLOWED` | EMAIL | `details.recipient` 指名 |
| `BODY_TOO_LARGE` | EMAIL | 內容太大 |
| `RUN_EMAIL_LIMIT` / `HOURLY_EMAIL_LIMIT` | EMAIL | 超過寄信上限 |
| `SMTP_AUTH` | EMAIL | 認證失敗（歸 blocked：重試一百次還是錯） |
| `TLS_REQUIRED` | EMAIL | 升不上 TLS |

政策放行但上游打不通（DNS、連線被拒、逾時、SMTP 連不上）→ `EGRESS_FAILED`（502，retryable）。
`422` 的請求錯誤**不用** `reason`，用 `details.header` / `details.field`。

## 稽核：`egress_calls`

**通過 token 驗證之後的每一條 return 路徑都要先寫一列**，含被擋的與 `422` 的。唯一不寫的是連 token 都沒過（沒有可信的 `run_id`）。
只記成功的話，「為什麼我的 agent 連不出去」在畫面上一點線索都沒有。

| 情況 | `error` | `status_code` | `rule_id` |
|---|---|---|---|
| 被政策擋下 | `EGRESS_BLOCKED: <reason>` | `null` | 命中規則時有 |
| 請求本身寫錯 | `VALIDATION_ERROR: <細節>` | `null` | 通常 `null` |
| 上游打不通 | `EGRESS_FAILED: <細節>` | `null` | 有 |
| 送出去了（含上游 4xx/5xx） | `null` | 上游的碼 | 有 |

- `path` **不存 query string**（那裡常放 token，稽核表不該變成第二個外洩點）。
- 寄信：`kind = EMAIL`、`host` = SMTP 主機、`path` = 收件人（`, ` 串接）。
- 只增不刪，沒有保留期（已知會長大，記在 BOARD）。
