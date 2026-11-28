# 容器約定 `zentra.image/v1`（任何語言）

平台不管 image 裡用什麼語言或框架，只看這份約定。Python + LangGraph 的寫法（`specs/agent.md`）是用 `zentra/agent-base` 與 `zentra_sdk` 實作這份約定的一種方式；Node、Go、Rust 或任何能發 HTTP 的程式，照這裡做也能發布成一個單位。

範例：`examples/node-agent/`（Node.js，沒有任何套件依賴）。

## 1. Image 要有什麼

| 項目 | 要求 |
|---|---|
| label | `io.zentra.image-contract=zentra.image/v1`，少了 INSPECT 就擋 |
| 執行檔 | `/opt/zentra/bin/zentra-agent`，可執行；平台用它當 entrypoint，第一個參數是模式 `validate`、`run`、`serve` |
| 平台 | 和主機相同的 OS／架構（本機通常是 `linux/amd64` 或 `linux/arm64`） |
| 身分 | 要能以 uid/gid `65534` 執行 |
| 檔案系統 | 根目錄唯讀，只有 `/workspace` 可寫（512 MB tmpfs，`noexec`，不能在那裡放執行檔）；不要宣告 `VOLUME`，也不要依賴 `HOME` |
| 網路 | 沒有對外路由，只連得到 `ZENTRA_GATEWAY_URL`；不要開 port |

image 的 `ENTRYPOINT` / `CMD` 會被平台覆蓋，寫了也沒用。其他限制（記憶體、CPU、行程數、Run 時間）見 `specs/agent.md`〈容器限制〉。

## 2. 三種模式

平台只給下表的 env，**多一個少一個都不會發生**；不要期待別的變數。所有呼叫都打 `ZENTRA_GATEWAY_URL`（例 `http://app:8000/internal`），`Authorization: Bearer <token>`，body 是 JSON。

| 模式 | 何時 | env | 做完 |
|---|---|---|---|
| `validate` | 發布時一次 | `ZENTRA_GATEWAY_URL`、`ZENTRA_AGENT_VERSION_ID`、`ZENTRA_VALIDATION_TOKEN` | 回報入口後 exit 0 |
| `serve` | 常駐容器（預設路徑） | `ZENTRA_GATEWAY_URL`、`ZENTRA_AGENT_ID`、`ZENTRA_CONTAINER_TOKEN`、`ZENTRA_MAX_CONCURRENT_RUNS` | 永遠不退出 |
| `run` | 一次性沙盒（常駐容器起不來時的備援） | `ZENTRA_GATEWAY_URL`、`ZENTRA_RUN_ID`、`ZENTRA_RUN_TOKEN` | 跑完一筆 Run 後 exit |

### `validate`：告訴平台這個單位有哪些入口

```
POST {GATEWAY}/versions/{ZENTRA_AGENT_VERSION_ID}/validation    token = ZENTRA_VALIDATION_TOKEN
```

成功：

```json
{ "valid": true, "runtime_contract": "zentra.image/v1",
  "manifest": { "name": "hello-node", "version": "1.0.0" },
  "entrypoints": [{
    "name": "default",
    "description": "跟指定的人打招呼",
    "input_schema":  { "type": "object", "required": ["name"], "properties": { "name": { "type": "string" } } },
    "output_schema": { "type": "object", "required": ["greeting"], "properties": { "greeting": { "type": "string" } } }
  }],
  "error": null }
```

失敗（自己檢查發現問題時）：

```json
{ "valid": false, "runtime_contract": "zentra.image/v1", "manifest": null, "entrypoints": null,
  "error": { "code": "BUILD_FAILED", "message": "…", "run_id": null, "retryable": false,
             "details": { "stage": "VALIDATE" } } }
```

平台這側會再驗一次，不符合回 `422`，版本 `FAILED`：

- `entrypoints` 非空；每項**恰好** `name`、`description`、`input_schema`、`output_schema`；`name`、`description` 是非空字串，`name` 不重複。
- 兩個 schema 都是合法的 JSON Schema 2020-12，最外層 `type: "object"`。
- `manifest` 是物件。平台用的名稱與版號來自 push 的 repository 與 tag，不是 manifest。
- 驗證期間沒有 run token，不能呼叫 LLM、外部 HTTP、寄信；10 分鐘內要結束。
- 回報後 exit 0；非 0 也算失敗，log 最後 500 字會進錯誤訊息。

### `serve`：長輪詢取工作

```
GET {GATEWAY}/agents/{ZENTRA_AGENT_ID}/work    token = ZENTRA_CONTAINER_TOKEN
```

- 最多掛 25 秒。`204` = 沒有工作，立刻再打；`200` = 一筆工作（見下）。
- 同時處理的 Run 不超過 `ZENTRA_MAX_CONCURRENT_RUNS`，滿了先不要取。
- 取工作失敗（連線錯、`5xx`）→ 等 1 到 2 秒重試，**不要退出**：平台不會自動把退出的容器拉回來，要等 60 秒存活檢查。
- 一筆 Run 出錯只回報那一筆，不要讓整個程式掛掉（同容器的其他 Run 會一起失敗）。
- 取工作本身就是健康訊號，不需要另外的 healthcheck。

工作項：

```json
{ "run_id": "…", "run_token": "…", "entrypoint": "default", "input": {},
  "manifest": {}, "default_model": "gpt-4o-mini", "config": {}, "secret_names": [] }
```

`run_token` 只屬於這一筆 Run，之後這筆 Run 的所有呼叫都用它；**不要設成共用 HTTP client 的預設 header**，會串到同時在跑的別筆 Run。

### `run`：一次性沙盒

```
GET {GATEWAY}/runs/{ZENTRA_RUN_ID}/bootstrap    token = ZENTRA_RUN_TOKEN
```

回應和工作項相同，但沒有 `run_token`（已經在 env 裡）。跑完、回報結果後 exit。

## 3. 跑一筆 Run

1. 照 `entrypoint` 找到入口。`input` 平台已經用 `input_schema` 驗過。
2. 執行，過程中可以送事件、打 LLM、代打外部 HTTP、寄信（見第 4 節）。
3. 回報結果，**一筆 Run 恰好一次**：

```
POST {GATEWAY}/runs/{run_id}/result    token = run token
```

```json
{ "output": { "greeting": "你好，小明！" }, "error": null, "usage": null }
```

失敗：

```json
{ "output": null, "usage": null,
  "error": { "code": "USER_CODE_ERROR", "message": "…（≤ 4000 字）", "run_id": "…",
             "retryable": false, "details": { "stage": "RUN" } } }
```

- `output` 與 `error` 恰好一個非 null。`error.code` 必須是 `apps/api/enums.py` 的 `ErrorCode`，使用者程式出錯用 `USER_CODE_ERROR`。
- `output` 是物件。平台用入口的 `output_schema` 驗，不符合 → Run 以 `USER_CODE_ERROR` 失敗（`details.stage = "OUTPUT"`，`details.field` 指出哪個欄位）。
- `usage` 平台不採用（用量以 Gateway 的 LLM 稽核為準），送 `null` 即可。
- 回 `202`。相同內容重送 OK，內容不同 → `422`。`5xx` 或連線錯誤要退避重試。
- 最終狀態由平台決定，容器不送 `run.status`。
- 沒回報就結束：`serve` 模式的 Run 會卡在 `RUNNING` 直到逾時被收掉（`SANDBOX_TIMEOUT`）；`run` 模式平台看 exit code 判失敗。

## 4. Run 期間能呼叫的 Gateway

全部用這筆 Run 的 run token。細節（限制、錯誤、稽核）見 `specs/gateway.md`。

| 做什麼 | 呼叫 | 說明 |
|---|---|---|
| 送事件 | `POST /runs/{run_id}/events` | body 是陣列 `[{event_id, type, payload}]`，見下 |
| LLM | `POST /llm/v1/chat/completions` 或 `/llm/v1/responses` | OpenAI 相容格式，`Authorization: Bearer <run token>`；任何 OpenAI 相容 SDK 把 `base_url` 設成 `{GATEWAY}/llm/v1`、`api_key` 設成 run token 就能用。`model` 沒有指定時用工作項的 `default_model` |
| 外部 HTTP | `POST /egress/request` | `{url, method, headers, body, timeout_seconds}`，要先在 Console 加白名單 |
| 寄信 | `POST /notify/email` | `{to, subject, text, html}`，要先設寄件者與收件人名單 |

事件：

| `type` | `payload` | 用途 |
|---|---|---|
| `node.start` / `node.end` | `{"node": "名稱", "ts": "ISO 8601"}` | 步驟開始與結束 |
| `log` | `{"level": "info", "message": "…", …其他欄位}` | 進事件流（stdout 只進容器 log） |
| `custom` | `{"name": "…", "payload": {…}}` | 自訂事件 |
| `token` | 自訂 | 串流輸出片段 |
| `error` | 自訂 | 非致命錯誤 |

- `event_id` 是 UUID v7，重送同一事件沿用同一個 id（平台去重）。
- 不能送 `run.status`（`422`）。

## 5. 不變的底線

- 容器裡沒有任何長期憑證：LLM 金鑰、機密值、SMTP 密碼都在平台那側，容器只拿短命 token；機密只會以名字出現在 `secret_names`。
- 容器唯一的對端是 `app`，不能直連 postgres、redis、registry 或外網；連外一律經 Gateway 代打。
- 版本以 digest 識別。push 同一個 tag 是取代，不會改寫舊 digest。
