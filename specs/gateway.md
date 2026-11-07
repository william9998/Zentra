# Internal Gateway

> `app` 上的 `/internal/*`：**容器唯一的網路對端**，是 P4 的實作。
> 長期憑證（LLM 金鑰、機密、SMTP 密碼）全部留在這一側；容器只拿短命的 token 來換服務。
> 前端永遠不呼叫這些路由，它們也不列在公開 OpenAPI 裡。

## 為什麼要有它

容器跑的是使用者的程式碼。金鑰如果放進容器，「程式碼被攻陷」就等於「金鑰洩漏」，而且平台根本不知道金鑰被拿去做什麼。

金鑰留在 Gateway 這一側，最壞的情況只是「某一筆 Run 多花了錢」——**而那筆錢有紀錄**。

容器的網路沒有對外路由，所以容器想做的每一件對外的事——打 LLM、打 HTTP、寄信、回報事件——都只能經過這裡。

```
容器（沒有金鑰）                        Gateway（app）                  外面
  ctx.llm() ──帶 run token──▶  驗 token → 換上真金鑰 → 打供應商 ─────▶ OpenAI 等
  ctx.http  ──帶 run token──▶  驗 token → 查白名單 → 代打 ──────────▶ api.binance.com 等
  ctx.send_email() ─────────▶  驗 token → 查收件人 → 用 SMTP 寄 ────▶ 郵件伺服器
```

## 三種 token

| token | 發給誰 | 綁定 | 有效期 | 能打哪些路由 |
|---|---|---|---|---|
| **run token** | 一筆 Run（一次性沙盒放 env；常駐容器跟著工作項） | 一個 `run_id` | = 該 Run 的 `timeout_seconds` | `/runs/{自己}/*`、`/llm/*`、`/egress/*`、`/notify/*` |
| **container token** | 一個常駐容器 | `agent_id + container_uuid` | 30 天 | **只有** `GET /agents/{自己的 agent}/work` |
| **validation token** | 一個驗證容器 | 一個 `agent_version_id` | ≤ 600 秒 | **只有** `POST /versions/{自己}/validation`，而且該版本必須還在 BUILDING |

- 三種都是 `secrets.token_urlsafe(32)`，存在 Redis（看 [redis.md](redis.md)），用 `Authorization: Bearer <token>` 帶。
- **拿 A 的 token 去打 B 的東西 → `403 PERMISSION_DENIED`**；token 無效或已撤銷 → `401`。
- run token 在 Run 進入終態時撤銷（一次性沙盒在 finally，常駐路徑在 finalize）。撤銷後同一把一律 `401`。
- `/internal/*` 和 `/v1/*` 掛在同一個 app 上，主機的 `:8000` 也連得到。**「internal」不代表有網段封鎖，所以每一條路由都必須驗 token。**

## 路由

| Method | Path | 用哪種 token | 做什麼 |
|---|---|---|---|
| `GET` | `/internal/runs/{run_id}/bootstrap` | run | 一次性沙盒取這筆 Run 的資料 |
| `POST` | `/internal/runs/{run_id}/events` | run | 回寫事件（陣列），看 [dispatch.md](dispatch.md) §事件 |
| `POST` | `/internal/runs/{run_id}/result` | run | 回寫 output / error / usage，**只保存、不轉狀態** |
| `POST` | `/internal/llm/v1/{chat/completions \| responses}` | run | 代打 LLM（OpenAI 相容） |
| `POST` | `/internal/egress/request` | run | 代打對外 HTTP |
| `POST` | `/internal/notify/email` | run | 寄信 |
| `GET` | `/internal/agents/{agent_id}/work` | container | 常駐容器長輪詢取工作（25 秒；`200` 有工作 / `204` 沒工作） |
| `POST` | `/internal/versions/{version_id}/validation` | validation | 回報驗證結果與 entrypoints |
| `POST` | `/internal/registry/events` | **不用 token**（內容不被信任，註冊前會回頭向 registry 確認） | 內部 registry 的 push 通知 → 註冊版本，看 [agent-registry.md](agent-registry.md) §push 即註冊 |

### 每筆 Run 帶進容器的資料

一次性沙盒從 bootstrap 拿，常駐容器從工作項拿。**兩條路都要帶，只改一條等於沒改。**

```json
{ "run_id": "…", "entrypoint": "default", "input": {}, "manifest": {},
  "default_model": "gpt-4o-mini",
  "config": { "symbols": ["BTCUSDT"] },
  "secret_names": ["SMTP_PASSWORD"] }
```

- 常駐容器的工作項另外多一個 `run_token`：app 在容器**取走工作的那一刻**才發，有效期從那時算（排隊的時間不會吃掉它）。
- `default_model`、`config`、`secret_names` **每筆 Run 重新計算**：中途改了設定，下一筆就生效，已經在跑的不受影響。
- `config` 是頂層欄位，**不能併進 `manifest`**（manifest 是版本的不可變內容）。
- `secret_names` 只有排序後的名字：沒有值、沒有長度、沒有尾四碼。
- 驗證容器一律是 `config = {}`、`secret_names = []`，而且沒有任何路由讓它取設定。

### 結果與事件

- `POST /result`：body `{"output": {}, "error": null, "usage": {}}`，回 `202 {"accepted": true}`。
  第一次寫入會記 `result_received_at`；相同內容重送沒關係，**不同內容 → `422`**。
  常駐路徑收到結果後會送 `zentra.run.finalize`，最終狀態由 worker 決定。
- `POST /events`：body 是頂層陣列 `[{"event_id", "type", "payload"}]`，回 `202 {"accepted": N, "last_seq": 42}`。容器不能送 `run.status`。
- `POST /validation`：body `{"valid", "runtime_contract", "manifest", "entrypoints", "error"}`。只保存，不會轉成 PUBLISHED。

## LLM 代打

```
容器的 ctx.llm()（標準的 LangChain chat model：base_url = http://app:8000/internal/llm/v1、api_key = run token）
  ──▶ Gateway：驗 run token → 選金鑰 → 換上真金鑰 → 打供應商 → 計量 → 寫 llm_calls → 原樣回傳
```

- **路徑白名單**：只代理 `chat/completions` 和 `responses`，其他一律 `404`。這個代理握著真金鑰，能打什麼由平台決定。
- **選哪把金鑰**：這個 Agent 自己的憑證（`agent_llm_credentials`，Fernet 加密）→ 沒有就用平台預設（`ZENTRA_LLM_API_KEY` / `ZENTRA_LLM_BASE_URL`）。
  每一筆 `llm_calls` 都記 `credential_source`（`AGENT` 或 `PLATFORM`）——憑證是可以換的、版本卻不可變，所以要留下「那一次用的是誰的金鑰」。
- **轉發前會剝掉** `authorization`、`x-api-key`、`openai-organization`、`openai-project` 等 header。容器送來的是 run token，原樣轉出去就是拿內部憑證去問外部服務。
- **串流**：`chat/completions` 帶 `stream: true` 時，平台會強制加上 `stream_options.include_usage`，否則算不到 token。
- **計量**：`chat/completions` 讀 `prompt_tokens` / `completion_tokens`；`responses` 讀 `input_tokens` / `output_tokens`。
  `cost_usd` 依 `LLM_PRICES` 在**呼叫當下**算好存起來，之後改價不會回頭重算；沒設價格的 model 記 0。
- **失敗也要記**：供應商回錯誤時，照樣寫一筆 `llm_calls`（有 `status_code`、`error`），錯誤內容原樣回給容器。平台根本沒設金鑰 → `LLM_ERROR`。
- `default_model` 由平台算好送進容器（Agent 憑證裡的 → 平台預設 `ZENTRA_LLM_DEFAULT_MODEL`）。
  **SDK 不准自己補一個寫死的值**，否則 Console 上設的預設 model 永遠到不了。
- 逾時 `ZENTRA_LLM_TIMEOUT_SECONDS`（預設 120）。

## 設定、機密、對外能力：三層

| 層 | 存哪 | 會進容器嗎 |
|---|---|---|
| 設定 | `agent_settings`，明文 JSON | **會**，每筆 Run 進 `ctx.config` |
| 機密 | `agent_secrets`，Fernet 加密（因為要解回來才能登入 SMTP、打外部 API） | **永遠不會**，只有名字進 `ctx.secret_names` |
| 能力 | `agent_egress_rules`、`agent_email_sender` | 規則不進容器；容器只看得到「請求成不成功」 |

三層都綁 Agent、不綁版本。沒設 `ZENTRA_SECRET_KEY` 時拒絕儲存機密（不會退而求其次存明文）。端點看 [api.md](api.md)。

> ⚠️ **通用的對外代打是一條真實的外洩通道。**
> 白名單擋得住「送去哪」，擋不住「送了什麼」——GET 的 query string 本身就是資料。
> 大小和次數上限只能壓低頻寬。這個取捨成立的前提是「只跑自己信得過的程式碼」。

## 代打對外 HTTP：`POST /internal/egress/request`

容器想打 `https://api.binance.com/...`，要先在這個 Agent 的白名單加一條規則（`POST /v1/agents/{id}/egress-rules`），然後經過這裡。

請求：

```json
{ "url": "https://api.binance.com/api/v3/klines?symbol=BTCUSDT", "method": "GET",
  "headers": { "Accept": "application/json" }, "body": null, "timeout_seconds": 10 }
```

回應（注意：就算上游回 404，這裡也是 `200`，上游的碼在 `status`）：

```json
{ "status": 200, "headers": { "content-type": "application/json" }, "body": "…" }
```

### 硬規則

1. **信封語意**：只要請求真的送出去了，Gateway 就回 `200`，上游的狀態碼放在 `status`。上游 4xx/5xx 不是平台的錯誤——Agent 要看得到狀態碼，才寫得出「主要來源失敗就換備援」。
2. **容器不能自帶認證**：`headers` 只准 `Accept` 和 `Content-Type`，其他 → `422`，`details.header` 會指名（不是 `EGRESS_BLOCKED`）。
   要認證的話，在規則上設 `auth_secret_name` + `auth_header`，由平台解密後加上去。
   平台固定送 `User-Agent: Zentra-Egress/1`（**改之前要重新驗證各交易所**：OKX 會擋 `Python-urllib/*`，改壞的症狀是備援來源全掛）。
3. **不跟隨 redirect**：回 3xx → `EGRESS_BLOCKED`（`REDIRECT`，`details.location`）。跟隨的話，對方一行 302 就能繞過白名單。
4. **`http` 和 `https` 都可以**，其他 scheme → `SCHEME_NOT_ALLOWED`。port 不限（沒寫就是 80 / 443）。
5. **送出時不檢查目的位址是不是內網**：IP、內網位址都能連，只要白名單上有那個主機。
   白名單**不准加平台自己**：`postgres`、`redis`、`registry`、`app`、`worker`、`localhost`、`127.0.0.0/8`、`::1` → `422`（見 [api.md](api.md)）。
   ⚠️ 這只擋**名字**，擋不住換個寫法連回平台：
   - 平台服務在 `zentra-net` 上的 IP（例如 `172.26.0.x`）。
   - `host.docker.internal` 加上平台開在主機上的 port：`8000`（管理 API）、`6000`（registry）、`5432`、`6379`。已實測連得到。
   要連主機上的其他服務，用 `host.docker.internal`。**白名單是唯一的防線**，加規則前想清楚。
6. **沒有任何旗標或環境變數可以關掉第 3 點**，連「預設關閉」的也不准存在——存在的旁路遲早會被打開，而且打開的時候沒有任何症狀。
7. **上限**（環境變數可調）：request body ≤ 64 KiB、response ≤ 1 MiB、`timeout_seconds` 預設 10 上限 15、每筆 Run 最多 `EGRESS_MAX_CALLS_PER_RUN`（100）次。
   **超量一律當錯誤，不截斷、不夾值**：例如 `timeout_seconds > 15` 是 `422`。
   次數計的是「通過認證的嘗試」，**被擋的也算**（失控的迴圈通常就是一直失敗的那種）。計數來源就是 `egress_calls` 這張表，不另外設計數器。
8. 回傳的 header 只有白名單：`content-type`、`content-length`、`date`、`retry-after`、`x-ratelimit-*`。

## 寄信：`POST /internal/notify/email`

```json
{ "to": ["me@example.com"], "subject": "…（≤ 200 字）", "text": "…", "html": null }
```

成功回 `202 {"accepted": true, "message_id": "<…@zentra>"}`。

規則：
1. 寄件者、帳號、密碼、SMTP 主機**全部由平台決定**（`agent_email_sender` + 機密）。request 帶 `from`、主機或密碼 → `422`。
2. 收件人逐一比對 `allowed_recipients`，**只要有一個不在名單上，整封都不寄**（`RECIPIENT_NOT_ALLOWED`）。
3. 一律要 TLS（587 用 STARTTLS、465 用隱式 TLS），升不上去 → `TLS_REQUIRED`。
4. 上限：內容 ≤ 256 KiB；每筆 Run ≤ `EMAIL_MAX_PER_RUN`（5）封；每個 Agent **滾動 60 分鐘** ≤ `EMAIL_MAX_PER_HOUR`（60）封。
   都計「嘗試」，包含認證失敗（反覆認證失敗正是 Gmail 鎖帳號的主因）。
5. `202` 只表示 SMTP 伺服器收下了，不表示對方讀到。**不冪等**：重送就是再寄一封。不支援附件、排程、退信處理。
6. 沒設寄件者 → `EGRESS_BLOCKED`（`SENDER_NOT_CONFIGURED`），不會默默不寄。

## `EGRESS_BLOCKED` 的 `details.reason`

被擋時，`details.reason` 告訴你為什麼：

| reason | 出現在 | 意思 |
|---|---|---|
| `NO_RULES` | HTTP | 這個 Agent 一條白名單都沒有 |
| `HOST_NOT_ALLOWED` / `METHOD_NOT_ALLOWED` / `PATH_NOT_ALLOWED` | HTTP | 不符合規則 |
| `SCHEME_NOT_ALLOWED` | HTTP | 不是 http / https |
| `REDIRECT` | HTTP | 上游回 3xx |
| `REQUEST_TOO_LARGE` / `RESPONSE_TOO_LARGE` | HTTP | 超過大小上限 |
| `RUN_CALL_LIMIT` | HTTP | 超過每 Run 次數上限 |
| `SENDER_NOT_CONFIGURED` | EMAIL | 沒設寄件者 |
| `RECIPIENT_NOT_ALLOWED` | EMAIL | 收件人不在名單，`details.recipient` 指名是誰 |
| `BODY_TOO_LARGE` | EMAIL | 內容太大 |
| `RUN_EMAIL_LIMIT` / `HOURLY_EMAIL_LIMIT` | EMAIL | 超過寄信上限 |
| `SMTP_AUTH` | EMAIL | 認證失敗（歸在 blocked：重試一百次還是錯） |
| `TLS_REQUIRED` | EMAIL | 升不上 TLS |

政策放行了、但對方打不通（DNS 失敗、連線被拒、逾時、SMTP 連不上）→ `EGRESS_FAILED`（502，可重試）。
請求本身寫錯的 `422`，**不用** `reason`，用 `details.header` / `details.field` 指出問題在哪。

## 稽核：`egress_calls`

**通過 token 驗證之後的每一條回傳路徑，都要先寫一列**，包含被擋的和 `422` 的。
唯一不寫的是「連 token 都沒過」的（沒有可信的 `run_id`）。

為什麼連失敗的也寫：只記成功的話，「為什麼我的 Agent 連不出去」在畫面上會沒有任何線索。

| 情況 | `error` | `status_code` | `rule_id` |
|---|---|---|---|
| 被政策擋下 | `EGRESS_BLOCKED: <reason>` | `null` | 命中規則時有 |
| 請求本身寫錯 | `VALIDATION_ERROR: <細節>` | `null` | 通常 `null` |
| 上游打不通 | `EGRESS_FAILED: <細節>` | `null` | 有 |
| 送出去了（含上游 4xx/5xx） | `null` | 上游的碼 | 有 |

- `path` **不存 query string**（那裡常放 token，稽核表不該變成第二個外洩點）。
- 寄信：`kind = EMAIL`、`host` = SMTP 主機、`path` = 收件人（用 `, ` 串接）。
- 只增不刪，沒有保留期限（已知會一直變大，記在 BOARD）。
