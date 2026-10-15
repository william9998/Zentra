# API 契約 (API Contract)

| 欄位 | 值 |
|---|---|
| 契約版本 | `v0.13.0` |
| 擁有者 | `@architect` |
| 對齊 Spec | `specs/SPEC.md v0.7.0` |

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

> **v0.3.0 依 ADR-0008 將尚未實作的 bundle upload 改為 OCI image digest registration**。
> Runs、SSE、MCP 的呼叫形狀不變；Agent Version 的 artifact 與 validation 契約改為 image。
>
> **v0.3.1 裁決 `trigger` 是平台接入協定。** `/v1` HTTP（包含 Console）固定 `API`，
> FastMCP handler 固定 `MCP`；移除無可信判定方式的 `CONSOLE`。
>
> **v0.4.0 依 ADR-0009 R1 新增 image tar 上傳這條進場路徑**（`POST /v1/agents/{id}/versions`
> 同時接 JSON 與 multipart）。**⚠️ 這些新增已由 v0.4.1 全部撤回，請勿實作。**
>
> **v0.12.0 依 [ADR-0014](./adr/ADR-0014-per-agent-config-secrets-and-egress.md) 新增三層
> per-agent 能力**（ZEN-019）：**設定**（§2.6）、**機密**（§2.7）、**對外出口**（§2.8 白名單、
> §2.9 寄件者、§2.10 稽核查詢）。Internal Gateway 的 bootstrap 與工作項新增 `config`／`secret_names`
> 兩個欄位（§4.1），並新增出口 proxy（§4.2）與寄信（§4.4）兩條路由。
> **Runs／SSE／MCP／版本發布的形狀完全未變。**
> `EGRESS_BLOCKED` 的語意擴充、新增 `EGRESS_FAILED`，見 §0。
>
> **v0.12.1 收斂 @reviewer 在寫 ZEN-019 驗收條件時挖出的九處自相矛盾或未定義**
> （U1–U9，逐條見 §5 變更紀錄）。**沒有新端點、沒有新欄位、沒有新錯誤 code**，
> 只有兩處是行為變更：**§4.1 第 4 點的 validation 設定語意整個反轉**（原本要求帶當下設定，
> 改為一律空），以及 **§4.3 移除 `HEADER_NOT_ALLOWED`**（那一條是 `422` 不是 `403`）。
> 其餘七條是把原本讀得出兩種答案的地方定死。

> **v0.4.1 依 [ADR-0009 修訂 R2](./adr/ADR-0009-internal-registry-image-upload.md) 撤回 v0.4.0 的全部新增。**
> 使用者改為**在平台主機上直接 `docker push localhost:6000/<repo>:<tag>`**，
> 再用**本來就有的** JSON 路徑以 digest 註冊。因此：
> multipart 形狀、`artifact.origin`、digest 三欄的 `null` 語意、`details.stage` 的 `LOAD`/`PUSH`、
> `QUOTA_EXCEEDED` 的磁碟語意 **全部移除**。
> **v0.4.0 的新增從未被實作**（`apps/` 零命中），所以相對於實際跑著的 **v0.3.1，本次是零變更**——
> 這是在墨水乾掉之前擦掉，不是撕毀已發布的契約。

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
| `QUOTA_EXCEEDED` | 429 | true | 超過**併發 Run 上限**（預設 10）。含 `Retry-After`。v0.4.0 曾放寬到含磁碟餘裕，v0.4.1 撤回 |
| `AGENT_NOT_PUBLISHED` | 409 | false | Agent 版本未發布 |
| `VERSION_CONFLICT` | 409 | false | 同一 Agent 的這個 semver 正在驗證中（`BUILDING`），等它結束再送；另見 §2.7、§2.9 的用法 |
| `BUILD_FAILED` | — | false | 版本發布失敗（寫在 `agent_versions.error`） |
| `SANDBOX_TIMEOUT` | — | true | 沙盒執行逾時（寫在 Run 的 error，非 HTTP） |
| `SANDBOX_ERROR` | — | true | 沙盒啟動或崩潰 |
| `LLM_ERROR` | — | true | Internal Gateway 呼叫模型供應商失敗 |
| `EGRESS_BLOCKED` | 403 | false | **沙盒的出口請求被平台政策擋下**（v0.12.0 擴充語意，原本只表示「連了 Gateway 以外的位址」）。涵蓋 §4.2 的 HTTP proxy 與 §4.4 的寄信，具體原因在 `details.reason`（枚舉見 §4.3）。**不可重試** —— 政策不會因為再打一次就改變 |
| `EGRESS_FAILED` | 502 | true | **政策放行了，但上游打不通**（v0.12.0 新增）：DNS 失敗、連線被拒、逾時、SMTP 連不上。與 `EGRESS_BLOCKED` 的分界是「該不該重試」 |
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

只接受上表定義的 top-level 欄位；包含 `trigger` 在內的未知欄位回 `422 VALIDATION_ERROR` 且不建立 Run。
任何 client header（包含 `X-Zentra-Trigger`、`Origin`、`Referer`）都不能改寫 Run trigger。

**Trigger 的權威來源**：

- 本端點與 `runs:invoke` 由 REST router 在伺服器內固定寫入 `API`。
- Console 使用同一個端點，因此也寫入 `API`。同源只代表部署拓撲，不是可驗證的 caller identity。
- MCP `tools/call` 由 FastMCP handler 在伺服器內固定寫入 `MCP`。
- request body/header 都沒有 discriminator。若未來需要可信的 UI caller 稽核，必須先有 session/API-key 認證，
  再另增 server-derived 欄位並 bump 契約；不得用可偽造 header 補洞。

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
| `trigger` | `API` \| `MCP`；表示 server-known ingress protocol。Console 經 REST 建立的 Run 為 `API` |
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
Query：`agent_id`、`api_key_id`、`status`、`trigger`、`created_after`、`created_before`、`limit`、`cursor`
```json
{ "items": [ /* Run 精簡物件，不含 input/output */ ], "next_cursor": null }
```

每一列含 `agent_id` **與 `agent_name`**（v0.11.0）：只給 id，列表就得靠人肉比對 UUID；
只給名稱又跳不到那個 Agent。兩個都要。

---

## 2. Agents 與版本

| Method | Path | 說明 |
|---|---|---|
| `GET` | `/v1/agents` | 列表 |
| `POST` | `/v1/agents` | 建立 Agent（只建 metadata，還沒有版本） |
| `GET` | `/v1/agents/{id}` | 詳情，含 `endpoints` |
| `PATCH` | `/v1/agents/{id}` | 改 metadata（**不可改已發布版本**，P5） |
| `DELETE` | `/v1/agents/{id}` | **硬刪除**，連容器與 image 一起（見 §2.5） |
| `GET` | `/v1/agents/{id}/versions` | 版本列表 |
| `POST` | `/v1/agents/{id}/versions` | **註冊 OCI image digest 並發布**；同版號 = 取代（見下） |
| `GET` | `/v1/agents/{id}/versions/{semver}` | 這個版號**最近一次註冊**的詳情，含 `entrypoints` 與 build 錯誤 |
| `GET` | `/v1/agents/{id}/openapi.json` | 該 Agent 的 OpenAPI 3.1 schema（P3） |
| `PUT` | `/v1/agents/{id}/llm-credential` | 註冊／覆寫這個 Agent 的 LLM 憑證（見 §2.1） |
| `GET` | `/v1/agents/{id}/llm-credential` | 憑證的 metadata。**永遠不含金鑰** |
| `DELETE` | `/v1/agents/{id}/llm-credential` | 移除憑證，退回用平台預設金鑰 |
| `GET` | `/v1/agents/{id}/containers` | 這個 Agent 的常駐容器狀態（見 §2.2） |
| `POST` | `/v1/agents/{id}/containers` | 啟動常駐容器。回 `202` |
| `POST` | `/v1/agents/{id}/containers/{cid}/restart` | 重啟。回 `202` |
| `DELETE` | `/v1/agents/{id}/containers/{cid}` | 停止。回 `202` |
| `POST` | `/v1/agents/{id}/api-keys` | 發一把下游 key。回 `201`，**明文只出現這一次**（見 §2.3） |
| `GET` | `/v1/agents/{id}/api-keys` | key 列表，**含已撤銷的**。永不含 key 本身 |
| `DELETE` | `/v1/agents/{id}/api-keys/{key_id}` | 撤銷一把 key。回 `200` 帶 `revoked_at`（軟撤銷） |
| `GET` | `/v1/agents/{id}/usage` | LLM 用量：總計、依 key、依 model（見 §2.4） |
| `GET` | `/v1/agents/{id}/llm-calls` | 逐筆 LLM 呼叫，可篩可翻頁（見 §2.4） |
| `GET` | `/v1/agents/{id}/config` | 這個 Agent 的可變設定，**明文回傳**（見 §2.6） |
| `PUT` | `/v1/agents/{id}/config` | **整份取代**設定文件。沒有 PATCH（見 §2.6） |
| `GET` | `/v1/agents/{id}/secrets` | 機密的名字清單。**永遠不含值**（見 §2.7） |
| `PUT` | `/v1/agents/{id}/secrets/{name}` | 寫入／覆寫一個機密。寫得進去讀不出來（見 §2.7） |
| `DELETE` | `/v1/agents/{id}/secrets/{name}` | 刪除一個機密。回 `204`，**硬刪**（見 §2.7） |
| `GET` | `/v1/agents/{id}/egress-rules` | 對外主機白名單（見 §2.8） |
| `POST` | `/v1/agents/{id}/egress-rules` | 新增一條白名單規則。回 `201` |
| `PUT` | `/v1/agents/{id}/egress-rules/{rule_id}` | 整條取代 |
| `DELETE` | `/v1/agents/{id}/egress-rules/{rule_id}` | 刪除一條規則。回 `204` |
| `GET` | `/v1/agents/{id}/email-sender` | 寄件者設定。**永遠不含密碼**（見 §2.9） |
| `PUT` | `/v1/agents/{id}/email-sender` | 設定／覆寫寄件者 |
| `DELETE` | `/v1/agents/{id}/email-sender` | 移除寄件者設定。回 `204`，之後寄信一律 `EGRESS_BLOCKED` |
| `GET` | `/v1/agents/{id}/egress-calls` | 逐筆對外呼叫與寄信的稽核紀錄（見 §2.10） |

> `PUT /v1/agents/{id}/aliases/{alias}`（流量切換）於 v0.2.0 移除。
> 呼叫時不指定 `version` 即用最新 `PUBLISHED` 版本。

### 2.2 常駐容器（v0.6.0 新增，ZEN-015）

依 [ADR-0012](./adr/ADR-0012-resident-agent-container.md)：一 Agent 一（或多）常駐容器。

**註冊不會啟動容器，但第一筆 Run 會**（v0.9.0 起）。

註冊是「這個版本存在」，那不該佔資源 —— 綁在一起會讓「發十個版本」變成
「開十個容器」。但**有人真的呼叫**就不一樣了：那時容器一定要存在，
要求使用者記得先按一個按鈕只是把平台的工作推給他。

所以現在的規則是：

- `POST /v1/agents/{id}/containers` 仍然在，意思變成**預熱** ——
  把冷啟動的成本挪到你方便的時候。
- 沒有可用容器時，第一筆 Run **會等**平台把容器拉起來（含 image pull），
  上限 `CONTAINER_START_TIMEOUT_SECONDS`（預設 180 秒）。
- 起不來就**退回一次性沙盒**，Run 照樣跑完。常駐是效能手段，
  它壞掉不該讓整個平台停擺。
- 閒置超過 `CONTAINER_IDLE_TIMEOUT_SECONDS`（預設 1800 秒）**自動停止**。
  自動產生的東西必須自動消失，否則「被呼叫過一次」等於「永久佔記憶體」。
  `inflight_runs > 0` 的容器不會被收。

```jsonc
// POST /v1/agents/{id}/containers   body 可省略，省略就用最新的 PUBLISHED
{ "version": "1.2.0" }
```

三條寫入端點都回 **`202`**：平台只登記期望狀態，實際建立／銷毀由 worker 做
（`api` 不掛 `docker.sock`，而且 image pull 可能很慢）。

```jsonc
{
  "container_id": "01a0…",
  "agent_id": "01a0…",
  "agent_version": "1.2.0",
  "status": "READY",              // STARTING | READY | UNHEALTHY | STOPPED | FAILED
  "inflight_runs": 3,
  "max_concurrent_runs": 30,      // SANDBOX_RUNS_PER_CONTAINER
  "restart_count": 0,
  "started_at": "…", "last_health_at": "…",
  "error": null
}
```

**硬規則**：

1. `status` 只有上面五個值。`READY` 表示**實際確認過容器還在跑**，
   不是「建立指令沒報錯」—— 一啟動就退出的容器必須是 `FAILED` 並在 `error` 帶上它的輸出。
2. `restart_count` 一定要回。一直重啟的容器是壞掉了，不是需要再重啟一次，
   而那件事在畫面上看起來跟「剛啟動」一模一樣。
3. 容器綁定單一版本（P5）。發新版要新容器，切換期間兩個並存。
4. 新增錯誤 code `AGENT_NOT_RUNNING`：呼叫了沒有 `READY` 容器的 Agent。
5. **一個 Agent 可以有多個容器**（v0.10.0）。現有容器全部滿載
   （每個 `inflight_runs` 都到 `max_concurrent_runs`）時，平台會在**背景**再開一個，
   上限 `SANDBOX_MAX_CONTAINERS_PER_AGENT`（預設 3）。
   - **這筆請求不會等新容器。** 工作已經排進佇列，先空出來的容器就會取走它；
     再讓它等一次冷啟動只是白白多幾十秒。新容器是為了**後面**的請求擴容。
   - 到達上限就只排隊，不再開。沒有上限的話一波尖峰會把主機的記憶體開光，
     那比讓請求排隊糟得多。
   - `inflight_runs` **永遠不會超過** `max_concurrent_runs`。滿載時進來的工作
     不預留給任何容器，等到真的有容器取走才記到它頭上。

### 2.3 Agent 的下游 API key（v0.7.0 新增，ZEN-015）

一個 Agent 可以發多把 key 給不同的下游應用。**key 買到的是「這筆用量是誰打的」，
不是「擋住未授權的人」** —— 這兩件事不一樣，別混：

- 契約 §0 寫明 `/v1` 的認證在 MVP 未實作，Console 目前也沒有帶 key 的能力。
- 所以預設行為是**有帶就必須有效，沒帶放行**。沒帶時 `runs.api_key_id` 是 `NULL`。
- 帶了無效或已撤銷的 key 一律 `401`；帶了**別的 Agent 的** key 是 `403`。
  「帶了但打錯」跟「沒帶」是不同的意圖，默默當成匿名會讓呼叫端以為用量有記到。
- 要真的擋人：`ZENTRA_REQUIRE_AGENT_API_KEY=true`，沒帶 key 就 `401`。
  **設之前先確認 Console 有辦法認證**，否則 Console 會整個不能用。

認證方式：`Authorization: Bearer zk_live_…`。REST 與 MCP 兩個門面**同一套規則**
（同一把 key 兩邊都能用，判斷一字不差）。

```jsonc
// POST /v1/agents/{id}/api-keys
{ "name": "下游應用名稱" }               // 必填，1–128 字元

// 201 —— key 欄位只在這裡出現過一次
{
  "key_id": "0199…",
  "agent_id": "0193…",
  "name": "下游應用名稱",
  "key": "zk_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",  // ← 只有這次
  "key_prefix": "zk_live_xxxx",          // 列表裡用來認出是哪一把
  "created_at": "2026-09-28T10:00:00Z",
  "last_used_at": null,
  "revoked_at": null
}
```

1. 平台只存 **SHA-256 雜湊**，不是密文。`key` 遺失只能重發一把，**沒有「再看一次」的端點**
   —— 這不是靠紀律，是靠沒有那個功能。與 §2.1 的 LLM 憑證刻意不同：
   那個要把明文轉發給供應商，只能加密；這個只要驗證身分，雜湊更安全。
2. **軟撤銷。** `DELETE` 回 `200` 帶 `revoked_at`，不是 `204`，而且那一列還在。
   硬刪會讓這把 key 過去的 Run 變成無主的帳。重複撤銷是冪等的。
3. `GET` **含已撤銷的 key**。藏起來的話，用量查詢會出現一把查不到名字的 key。
4. `runs.api_key_id` 記的是觸發那筆 Run 的 key。Console 發起的 Run 是 `NULL`。

### 2.5 刪除 Agent（v0.11.0 改，ADR-0013）

`DELETE /v1/agents/{id}` → `204`。**不可復原。**

同步刪掉（回 `204` 時已經不在了）：Agent、所有版本、容器紀錄、Run、事件、
LLM 呼叫紀錄、LLM 憑證、下游 API key，
以及 v0.12.0 起的**設定、機密、出口白名單、寄件者設定、出口稽核紀錄**（§2.6–§2.10）。

非同步清掉（交給 worker，它才碰得到 docker.sock 與 registry）：
常駐容器、主機上的 image、**registry 裡的 manifest**。

1. **稽核軌跡會一起消失。** SPEC §10 的「LLM 呼叫 100% 留存」在這個 Agent 上不再成立。
   要留就在刪除前匯出。UI 的確認視窗必須把這件事講出來。
2. **清理失敗不會讓刪除失敗。** Agent 已經不在了，回錯誤只會讓人以為沒刪成功、
   再按一次。結果寫在 worker 日誌裡。
3. **只有這條路徑會刪 manifest。** 沒有端點可以單獨刪掉某個版本的 image ——
   那仍然違反 P5。
4. 刪 manifest 只是標記可回收，**磁碟空間要 `registry garbage-collect` 才真的釋放**，
   而那要停機做。畫面上不要說「已釋放空間」。
5. 批次刪除由 Console 逐一呼叫這條端點，**沒有 bulk 端點** —— 一次刪一個的失敗
   邊界比較清楚：哪一個沒刪掉說得出來。

### 2.4 用量與除錯查詢（v0.8.0 新增，ZEN-015）

回答兩個問題，所以是兩條端點：

| 問題 | 端點 |
|---|---|
| 這把 key 花了多少、花在哪個模型上 | `GET /v1/agents/{id}/usage` |
| 這把 key 的**哪一筆** request 出錯了 | `GET /v1/agents/{id}/llm-calls` |

兩條共用同一組查詢參數（全部選填，可疊加）：

`api_key_id` · `since` · `until` · `model` · `credential_source`
（`llm-calls` 另有 `status_code` · `errors_only` · `limit` · `cursor`）

```jsonc
// GET /v1/agents/{id}/usage?api_key_id=…&since=2026-09-01T00:00:00Z
{
  "totals":   { "llm_calls": 4, "input_tokens": 340, "output_tokens": 140, "cost_usd": "0.000495" },
  "by_key":   [{ "key_id": "0199…", "name": "行銷站", "key_prefix": "zk_live_a1b2",
                 "llm_calls": 3, "input_tokens": 310, "output_tokens": 130, "cost_usd": "0.00045" },
               { "key_id": null, "name": null, "key_prefix": null,
                 "llm_calls": 1, "input_tokens": 30, "output_tokens": 10, "cost_usd": "0.000045" }],
  "by_model": [{ "model": "gpt-4o-mini", "llm_calls": 3, "input_tokens": 330,
                 "output_tokens": 140, "cost_usd": "0.000495" }]
}
```

```jsonc
// GET /v1/agents/{id}/llm-calls?api_key_id=…&errors_only=true
{
  "items": [{
    "call_id": "63a7…", "run_id": "01a0…", "run_status": "SUCCEEDED",
    "api_key_id": "0199…", "model": "gpt-4o",
    "input_tokens": 10, "output_tokens": 0, "cost_usd": "0",
    "latency_ms": 120, "status_code": 429, "error": "rate limited",
    "credential_source": "AGENT", "created_at": "2026-09-28T13:57:31Z"
  }],
  "next_cursor": null
}
```

1. **`by_key` 一定含 `key_id: null` 那一列** —— 那是沒帶 key 的呼叫（Console 發起的）。
   濾掉它總計就對不起來，而「這些用量是誰打的」通常正是在查的東西。
2. **`cost_usd` 是十進位字串**，不是數字。與 §1 的 Run `usage` 同一個理由：這是帳，
   浮點會變成對不起來的數字。前端只印，不算。
3. **歸戶靠 `runs.api_key_id` join**，`llm_calls` 沒有自己的 `api_key_id`。
   存兩份會有兩份可能不一致的真相，而不一致時無法判斷哪份是對的。
4. `errors_only=true` 等於 `error IS NOT NULL OR status_code >= 400`。
   給它一個開關是因為那是除錯的第一個動作。
5. `next_cursor` 與 §0 的游標同一種格式（`created_at` + `id` 的 base64），
   **無下一頁時是 `null`，key 一定在**。

### 2.1 Agent 的 LLM 憑證（v0.5.0 新增，ZEN-012）

**憑證綁 Agent，不綁版本。** 路徑上沒有 `{semver}`，這是刻意的：
發新版本不用重填，改金鑰則該 Agent 的**所有版本立刻一起變**。
與 P5 的互動見 [ADR-0011](./adr/ADR-0011-per-agent-llm-credentials.md)。

```jsonc
// PUT /v1/agents/{id}/llm-credential
{
  "api_key": "sk-…",                     // 必填，8–512 字元。寫進去就讀不出來
  "base_url": "https://api.openai.com/v1", // 選填。金鑰與端點通常是一組的
  "default_model": "gpt-4o-mini"          // 選填
}
```

回應（`PUT` 與 `GET` 同形狀）：

```jsonc
{
  "configured": true,
  "last_four": "WXYZ",     // 金鑰尾四碼，UI 唯一看得到的線索；不足四碼時是等長遮罩
  "base_url": "https://api.openai.com/v1",
  "default_model": "gpt-4o-mini",
  "updated_at": "2026-09-26T06:04:39.123896Z"
}
```

未設定時 `configured: false`，其餘欄位為 `null`。

**硬規則**：

1. **沒有任何端點回傳金鑰明文或密文。** 這不是「目前沒做」，是契約禁止。
2. 平台未設定 `ZENTRA_SECRET_KEY` 時，`PUT` 回 `422 VALIDATION_ERROR` ——
   **不接受憑證**，而不是退回明文儲存。
3. 刪除 Agent（軟刪）會**硬刪**其憑證。
4. Gateway 的金鑰選擇順序：Agent 自己的 → 平台預設。
   每一列 `llm_calls` 記錄 `credential_source`（`AGENT` | `PLATFORM`）。

---

> **§2.6–§2.10 是 [ADR-0014](./adr/ADR-0014-per-agent-config-secrets-and-egress.md) 的三層模型**
> （v0.12.0 新增，ZEN-019）。三層的差別**先看這張表再讀細節**，混用是最容易犯的錯：
>
> | 層 | 端點 | 讀得回來嗎 | 到得了沙盒嗎 |
> |---|---|---|---|
> | **設定** | §2.6 `/config` | **讀得回來，明文** | 會 —— 每筆 Run 隨 bootstrap／工作項送進 `ctx.config` |
> | **機密** | §2.7 `/secrets` | **永遠讀不回來**，只有尾四碼 | **永遠不會**。沙盒只拿得到名字清單 |
> | **能力** | §2.8 `/egress-rules`、§2.9 `/email-sender` | 讀得回來（不含密碼） | 規則本身不進沙盒；沙盒只看得到請求成不成功 |
>
> 三層都**綁 Agent 不綁版本**（同 §2.1）：發新版自動繼承，更新則所有版本立刻一起變。
> 刪 Agent 時三層**全部硬刪**（§2.5 / [ADR-0013](./adr/ADR-0013-hard-delete-agents-and-images.md)）。

### 2.6 Agent 的可變設定（v0.12.0 新增，ZEN-019）

一份 per-agent 的 JSON 文件，**原封不動**送進沙盒的 `ctx.config`。

```jsonc
// PUT /v1/agents/{id}/config —— 整份取代
{
  "config": {
    "symbols": ["BTCUSDT", "ETHUSDT"],
    "interval": "1h",
    "rsi_oversold": 30,
    "notify_to": "me@example.com"
  }
}
```

回應（`PUT` 與 `GET` 同形狀）：

```jsonc
{
  "config": { "symbols": ["BTCUSDT", "ETHUSDT"], "interval": "1h", "rsi_oversold": 30,
              "notify_to": "me@example.com" },
  "updated_at": "2026-09-30T06:04:39.123896Z"
}
```

未設定過時 `config: {}`、`updated_at: null`。**`config` 這個 key 一定在，不會是 `null`。**

**硬規則**：

1. **設定不是機密。** `GET` 回明文，任何能開 Console 的人都看得到，它也會**完整送進沙盒**。
   API key、密碼、token 一律放 §2.7。平台**不會**掃描內容、不會猜哪個值像 secret ——
   一個猜得不準的警告比沒有警告更糟，因為它會讓人以為有在幫忙檢查。
2. **只有整份取代，沒有 `PATCH`。** 要改一個鍵就 `GET` → 改 → `PUT`。
   兩種寫入語意等於兩條要測、要寫文件的路徑，而單租戶下沒有併發編輯的問題。
   要刪一個鍵，就 `PUT` 一份不含它的文件。
3. **鍵名**：`^[A-Za-z_][A-Za-z0-9_]{0,63}$`，最多 100 個頂層鍵。
   **值可以是任何 JSON**（字串、數字、布林、`null`、陣列、巢狀物件）—— 使用者要的是自由度，
   這裡給滿。整份序列化後 **≤ 64 KiB**，超過回 `422`。
   上限的理由很具體：這份文件**跟著每一筆 Run 走**，大文件是每筆 Run 都要付的成本。
4. **更新立刻對所有版本、所有進行中的容器生效**，下一筆 Run 就是新值。
   **已經在跑的 Run 不受影響** —— 它的 `ctx.config` 在派工那一刻就定稿了。
5. `PUT` 的 body 必須是 `{"config": {...}}`，**不是裸物件**。
   留這一層是為了之後要加 `updated_by` 之類的欄位時不必改形狀。

### 2.7 Agent 的機密（v0.12.0 新增，ZEN-019）

**寫得進去，讀不出來。** 沙盒拿得到的只有**名字**。

```jsonc
// PUT /v1/agents/{id}/secrets/SMTP_PASSWORD
{ "value": "abcd efgh ijkl mnop" }        // 必填，1–4096 字元
```

回應 **`200`**（`PUT` 與 `GET` 清單的每一列同形狀）：

```jsonc
{
  "name": "SMTP_PASSWORD",
  "last_four": "mnop",                     // 唯一看得到的線索；規則見下方第 7 點
  "created_at": "2026-09-30T06:00:00Z",
  "updated_at": "2026-09-30T06:04:39.123896Z"
}
```

**新建與覆寫都是 `200`，不分 `201`**（v0.12.1 定案，U9）。`PUT` 到一個具名資源是 upsert，
而呼叫端**事先無從知道會是哪一種**（先 `GET` 再 `PUT` 中間有 race），
逼它分支等於逼它處理一個它不在乎的區別。§2.1 的 `PUT /llm-credential` 也是兩種都 `200`。
真的想知道的話比較 `created_at` 與 `updated_at`：相等就是這次新建的。

```jsonc
// GET /v1/agents/{id}/secrets
{ "items": [ { "name": "SMTP_PASSWORD", "last_four": "mnop", "created_at": "…", "updated_at": "…" } ] }
```

**硬規則**：

1. **沒有任何端點回傳明文或密文。** 這不是「目前沒做」，是契約禁止 —— 與 §2.1 第 1 點同一條規則，
   同一個理由：靠「沒有那個功能」保證，不是靠紀律。
2. **Fernet 靜態加密，不是雜湊。** 這些值要拿去登入 SMTP、打外部 API，必須解得回來。
   （§2.3 的下游 API key 是 SHA-256 雜湊，那個只需驗證身分。**兩種模式別搞混。**）
   平台未設定 `ZENTRA_SECRET_KEY` 時 `PUT` 回 `422 VALIDATION_ERROR`，**不接受機密**，
   而不是退回明文儲存。
3. **名字**：`^[A-Za-z_][A-Za-z0-9_]{0,63}$`，每個 Agent 最多 50 個。
   名字**長得像環境變數，但它永遠不會變成環境變數** —— 沙盒的 env 是精確集合檢查
   （§4.1 第 3 點），機密一個都不會出現在那裡。
4. **沙盒只拿得到名字清單**（`secret_names`，見 §4.1）。給名字是為了讓 agent 能 fail fast
   說出「缺 `SMTP_PASSWORD`」，而不是跑完整段分析才在最後一步失敗。
   **值、長度、尾四碼一律不給。**
5. 重複 `PUT` 同一個名字是覆寫，`created_at` 保留、`updated_at` 更新。
   `DELETE` 回 `204`，**硬刪**，重複刪除冪等（不存在也回 `204`）。
6. 機密被別的地方**以名字引用**（§2.8 的 `auth_secret_name`、§2.9 的 `password_secret_name`）。
   **刪掉一個還被引用的機密，平台回 `409 VERSION_CONFLICT`**，`details.referenced_by` 列出引用者
   —— 直接刪掉會讓那條能力在下一次 Run 才無聲失敗。要刪就先解除引用。
7. **`last_four` 的定義（v0.12.1 補，U7）**：值長度 ≥ 4 時是最後四個字元；
   **< 4 時是 `*`（U+002A）重複「值的長度」次**，例如長度 2 → `"**"`。
   這是既有 `apps/api/services/secrets.py::last_four()` 的行為，契約把它寫死，
   不是引入新行為。這確實洩漏了「長度 < 4」與確切長度 —— 接受，
   因為短於四個字元的東西本來就不構成機密，而回一個固定長度的假遮罩
   會讓 UI 顯示得像一把正常的金鑰。

### 2.8 對外出口白名單（v0.12.0 新增，ZEN-019）

沙盒本身**沒有對外路由**（SPEC §6.2 L1）。這裡定義的是「平台願意替這個 Agent 代打哪些主機」。
執行面在 §4.2。

```jsonc
// POST /v1/agents/{id}/egress-rules → 201
{
  "host": "api.binance.com",         // 必填
  "methods": ["GET"],                // 選填，預設 ["GET"]
  "path_prefix": "/api/v3/",         // 選填，預設 null（該主機所有路徑）
  "auth_secret_name": null,          // 選填，指向 §2.7 的一個名字
  "auth_header": null,               // 選填，與 auth_secret_name 成對
  "description": "Binance 現貨行情"   // 選填，≤ 200 字元
}
```

回應（`POST` / `PUT` / `GET` 清單每一列同形狀）：

```jsonc
{
  "rule_id": "01a0…", "agent_id": "01a0…",
  "host": "api.binance.com", "methods": ["GET"], "path_prefix": "/api/v3/",
  "auth_secret_name": null, "auth_header": null, "description": "Binance 現貨行情",
  "created_at": "…", "updated_at": "…"
}
```

**硬規則**（來自 ADR-0014 §3 的 E1–E7，違反任何一條回 `422 VALIDATION_ERROR`）：

1. **預設拒絕，沒有萬用字元。** 一條規則都沒有 → 一個外部主機都連不到。
   `host` 含 `*`、含 scheme（`https://`）、含 port、含路徑、含大寫、
   或是 IP 字面值（v4／v6 都算）→ `422`。合法的只有小寫 DNS 名稱。
2. **`methods` 只接受 `GET` 與 `POST`**，預設 `["GET"]`，空陣列 `422`。
   要 `POST` 必須在**那一條規則上**明列 —— 不是全域開關。
3. **`auth_secret_name` 與 `auth_header` 成對**：有一個沒有另一個 → `422`。
   `auth_secret_name` 指向的機密不存在 → `422`。
   `auth_header` 不得是 `Accept` / `Content-Type` / `Host` / `Content-Length`。
4. **`host` 在同一個 Agent 內唯一。** 撞名回 `409 VERSION_CONFLICT`。
   要同一主機兩種待遇（不同 `path_prefix`）→ MVP 不支援，把 `path_prefix` 放寬到共同前綴。
   理由是「哪一條規則生效」必須一眼看得出來，重疊規則的優先序是 bug 的溫床。
5. 每個 Agent 最多 20 條規則。
6. **改規則立刻生效**，包括對已經在跑的 Run 的下一次呼叫。

### 2.9 寄件者設定（v0.12.0 新增，ZEN-019）

SMTP 不是 HTTP，**不走 §2.8 的白名單**，獨立一份設定（ADR-0014 §4）。

```jsonc
// PUT /v1/agents/{id}/email-sender
{
  "smtp_host": "smtp.gmail.com",
  "smtp_port": 587,
  "username": "me@gmail.com",
  "from_address": "me@gmail.com",
  "password_secret_name": "SMTP_PASSWORD",       // 指向 §2.7，密碼不在這裡
  "allowed_recipients": ["me@gmail.com"]         // 必填，非空
}
```

回應（`PUT` 與 `GET` 同形狀）：

```jsonc
{
  "configured": true,
  "smtp_host": "smtp.gmail.com", "smtp_port": 587,
  "username": "me@gmail.com", "from_address": "me@gmail.com",
  "password_secret_name": "SMTP_PASSWORD",
  "allowed_recipients": ["me@gmail.com"],
  "updated_at": "2026-09-30T06:04:39.123896Z"
}
```

未設定時 `configured: false`，其餘欄位 `null`。

**硬規則**：

1. **這張表沒有密碼欄位。** 密碼只能以 `password_secret_name` 指名（§2.7）。
   body 出現 `password` 之類的欄位 → `422`，訊息要指出該去哪裡存。
   只留一條存機密的路，是為了不要有第二種加密方式與第二種洩漏方式。
2. **`allowed_recipients` 必填、非空、最多 10 個精確地址，不接受網域萬用字元。**
   少了它，一支迴圈寫錯的 agent 會用使用者的 Gmail 帳號變成垃圾信來源，
   而 Google 封的是**那個帳號**。
3. `smtp_port` ∈ `{587, 465, 25}`；`587` 走 STARTTLS、`465` 走隱式 TLS。
   **明文 SMTP（`25` 且不升級）不支援** —— 平台一律要求 TLS，升不上去就 `EGRESS_BLOCKED`。
4. `password_secret_name` 指向的機密不存在 → `422`。
5. `DELETE` 回 `204`。之後 §4.4 一律 `EGRESS_BLOCKED`（`reason: SENDER_NOT_CONFIGURED`），
   **不是靜默不寄** —— agent 要知道信沒寄出去。

### 2.10 出口稽核查詢（v0.12.0 新增，ZEN-019）

`GET /v1/agents/{id}/egress-calls` —— **HTTP 出口與寄信在同一張表**，用 `kind` 分。
「這個 Agent 往外送了什麼」是一個問題，不該有兩個要分別查的地方。

查詢參數（全部選填，可疊加）：`kind`（`HTTP` | `EMAIL`）· `run_id` · `since` · `until` ·
`host` · `status_code` · `errors_only` · `limit` · `cursor`。

```jsonc
{
  "items": [{
    "call_id": "63a7…", "run_id": "01a0…", "kind": "HTTP",
    "rule_id": "01a0…", "method": "GET",
    "host": "api.binance.com", "path": "/api/v3/klines",
    "status_code": 200, "request_bytes": 0, "response_bytes": 18422,
    "latency_ms": 310, "error": null, "created_at": "2026-09-30T13:57:31Z"
  }, {
    "call_id": "63a8…", "run_id": "01a0…", "kind": "EMAIL",
    "rule_id": null, "method": null,
    "host": "smtp.gmail.com", "path": "me@example.com",
    "status_code": null, "request_bytes": 812, "response_bytes": 0,
    "latency_ms": 940, "error": null, "created_at": "2026-09-30T13:57:33Z"
  }],
  "next_cursor": null
}
```

1. **query string 不入庫。** `path` 只存路徑，不存 `?` 後面的東西 ——
   那裡常常就是使用者放 token 的地方，稽核表不該變成第二個機密外洩點。
   需要重現完整請求時，`rule_id` + `path` + `run_id` 已經夠定位。
2. **`kind=EMAIL` 時 `path` 放收件人**（多個以 `, ` 串接），`host` 放 SMTP 主機。
   欄位共用而不是各開一組，是為了讓「往外送了什麼」只有一種查法。
3. `errors_only=true` 等於 `error IS NOT NULL OR status_code >= 400`（同 §2.4 第 4 點）。
4. **每一個通過認證的請求都寫一列，失敗的也要寫**（v0.12.1 收斂，U3）。
   只記成功的話，「為什麼我的 agent 連不出去」在畫面上完全沒有線索。
   `error` 欄位存的是**錯誤 code 加可辨識的原因**，三種形狀：

   | 情況 | `error` | `status_code` | `rule_id` |
   |---|---|---|---|
   | 被政策擋下 | `EGRESS_BLOCKED: <reason>`（reason 見 §4.3） | `null` | 命中規則時有，沒命中時 `null` |
   | 請求本身寫錯（如自帶 header，§4.2 第 2 點） | `VALIDATION_ERROR: <細節>` | `null` | 通常 `null` —— 這類檢查在比對規則之前 |
   | 上游打不通 | `EGRESS_FAILED: <細節>` | `null` | 有 |
   | 送出去了 | `null`（即使上游回 4xx／5xx） | 上游的碼 | 有 |

   **上游回 4xx／5xx 時 `error` 是 `null`** —— 那不是平台錯誤（§4.2 第 1 點的信封語意），
   要找它們用 `status_code` 篩，`errors_only=true` 兩種都涵蓋（第 3 點）。
   **唯一不寫列的情況是連認證都沒過**（`401`／`403`），那時沒有可信的 `run_id`。
5. `next_cursor` 與 §0 的游標同格式，**無下一頁時是 `null`，key 一定在**。

### Agent metadata

`POST /v1/agents` Request：

```json
{ "name": "research-agent", "description": "研究與摘要" }
```

Response `201`：

```json
{
  "agent_id": "0192...",
  "name": "research-agent",
  "description": "研究與摘要",
  "latest_published_version": null,
  "created_at": "2026-09-22T08:00:00Z"
}
```

`GET /v1/agents` Response `200`：

```json
{
  "items": [{
    "agent_id": "0192...",
    "name": "research-agent",
    "description": "研究與摘要",
    "latest_published_version": "1.2.0",
    "updated_at": "2026-09-22T08:10:00Z"
  }],
  "next_cursor": null
}
```

`GET /v1/agents/{id}` 除上述欄位外，必須包含下方 `endpoints`。列表只回未 soft-delete 的 Agent。

### `POST /v1/agents/{id}/versions` — 以 digest 註冊 OCI image 並發布

**只有一個形狀**：`Content-Type: application/json` + canonical digest reference。
其他 Content-Type（含 `multipart/form-data`）→ `422 VALIDATION_ERROR`。
**平台不接收 image 檔案**（[ADR-0009 R2](./adr/ADR-0009-internal-registry-image-upload.md)）。

image 本體放在哪裡，對這個端點**沒有任何差別**——不分支、不記錄、沒有對應欄位：

| image 放哪裡 | 使用者怎麼放進去 | `image_ref` 長相 |
|---|---|---|
| 外部 Registry | 自己 push 到 GHCR / 自建 registry | `ghcr.io/acme/agent@sha256:…` |
| **平台內部 Registry** | **在跑平台的那台主機上** `docker push localhost:6000/<repo>:<tag>` | `localhost:6000/<repo>@sha256:…` |

```json
{
  "semver": "1.2.0",
  "image_ref": "ghcr.io/acme/research-agent@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
}
```

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| `semver` | string | 是 | Agent 內唯一；發布後不可重用 |
| `image_ref` | string | 是 | canonical OCI digest reference；只接受 `repository@sha256:<64 hex>`，tag-only 為 `422 VALIDATION_ERROR`。`repository` 可以含 registry host 與 port（`localhost:6000/my-agent` 合法） |

未定義的欄位（含 `origin`、registry 位址、任何憑證）→ `422 VALIDATION_ERROR`。

Response `202`：
```json
{
  "agent_version_id": "0192...",
  "semver": "1.2.0",
  "version_id": "0123456789ab",
  "source_type": "OCI_IMAGE",
  "artifact": {
    "image_ref": "ghcr.io/acme/research-agent@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "digest": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "platform": null,
    "size_bytes": null,
    "runtime_contract": null
  },
  "status": "BUILDING",
  "error": null,
  "created_at": "..."
}
```
| 欄位 | 說明 |
|---|---|
| `version_id` | OCI digest hex 的前 12 碼，只供短顯示；P5 的權威身份是 `artifact.digest` 完整值。**任何狀態下都非 `null`** |
| `source_type` | MVP 固定 `OCI_IMAGE`；不得出現第二個值 |
| `artifact.image_ref` | canonical digest reference，發布後不可變。**原封不動保存呼叫端送來的字串**——正規化只可以發生在比較時，不得改寫已保存的值 |
| `artifact.digest` | 完整 `sha256:<64 hex>`。**任何狀態下都非 `null`** |
| `artifact.platform` / `size_bytes` / `runtime_contract` | `BUILDING` 可為 `null`；inspect/validate 成功後必須有值 |
| `status` | `BUILDING` \| `PUBLISHED` \| `FAILED` |

> **v0.4.1 撤回**：`artifact.origin` 欄位不存在（v0.4.0 曾定義，從未實作）。
> `version_id` / `artifact.image_ref` / `artifact.digest` **在任何狀態下都不會是 `null`**。

發布是非同步的（`q.build`）。前端輪詢 `GET /v1/agents/{id}/versions/{semver}` 看 `status`。
版本詳情使用同一個 response shape，另含 `manifest` 與 `entrypoints`；兩者在 `BUILDING` 為 `null`，
`PUBLISHED` 時必須有值。`FAILED` 時 `error.code=BUILD_FAILED`，
`error.details.stage` 必須是 **`PULL`、`INSPECT`、`VALIDATE`** 之一。
（v0.4.0 曾加入 `LOAD` / `PUSH`，v0.4.1 撤回；**不得產生也不得斷言這兩個值**。）
`BUILD_FAILED` 一律 `retryable:false`：原請求重送不會有不同結果，修正 image 後重新註冊（同版號可以）。

**同版號重新註冊 = 取代**（v0.13.0，[ADR-0016](./adr/ADR-0016-same-semver-replaces-version.md)）：

1. 同一個 `agent_id + semver` 再送一次 → `202`，建一筆新的 `BUILDING`（新的 `agent_version_id`）。
2. 新的驗證通過才取代：舊的那筆從此不出現在任何版本端點，Run 與 MCP 改用新的。
   驗證失敗 → 新的那筆 `FAILED`，**舊的照常服務**。
3. 這個版號已有一筆 `BUILDING` → `409 VERSION_CONFLICT`。
4. `image_ref` 與現役那筆完全相同 → `202` 回現役那筆（`status: PUBLISHED`），不重 build。
5. `GET /versions/{semver}` 回**最近一次註冊**的那筆（輪詢靠它）；所以取代失敗後它回 `FAILED`，
   但呼叫這個版號跑的仍是舊的 `PUBLISHED`。`GET /versions` 列表兩筆都會出現。
6. 已建立的 Run 綁定建立當下的那一筆，取代不影響它；Run 詳情的 `version` 仍是 semver，
   要知道實際跑的 image 看 digest。

同一個 digest 可以被不同 Agent/semver 明確引用，但每一列仍是獨立不可變版本。

**`PUBLISHED` 的必要條件**：`image_ref` 指向的 digest 必須是**可以重新 pull 回來的**。
worker 在 `INSPECT` 時以 `RepoDigests` 確認這件事；`RepoDigests` 不含註冊的 reference 即
`BUILD_FAILED / stage=INSPECT`。沒有可重新取得的來源的版本**不得**成為 `PUBLISHED`（P5）。

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
| `GET` | `/internal/runs/{run_id}/bootstrap` | 取 manifest、entrypoint 名稱、input、`default_model`、**`config`、`secret_names`**（§4.1） |
| `POST` | `/internal/runs/{run_id}/events` | 回寫事件（批次，body 是事件陣列） |
| `POST` | `/internal/runs/{run_id}/result` | 回寫最終 output 或 error |
| `POST` | `/internal/llm/v1/chat/completions` | **OpenAI 相容**。平台換上真金鑰、計量 token、寫 `llm_calls` |
| `POST` | `/internal/egress/request` | **對外 HTTP 代打**（v0.12.0 新增）。白名單 + 平台注入認證，寫 `egress_calls`（§4.2） |
| `POST` | `/internal/notify/email` | **寄信**（v0.12.0 新增）。SMTP，密碼由平台解密後使用，寫 `egress_calls`（§4.4） |
| `GET` | `/internal/agents/{agent_id}/work` | 常駐容器 long-poll 取工作項（ZEN-015）。工作項含 **`config`、`secret_names`**（§4.1） |
| `POST` | `/internal/versions/{version_id}/validation` | `--mode=validate` 沙盒回報驗證結果與抽出的 entrypoints schema |

> **`default_model` 為什麼由平台算好再送（v0.9.0 新增）**
>
> 這是 `ctx.llm()` 沒指定 model 時要用哪個：Agent 憑證的 `default_model` → 平台預設。
> **SDK 不准自己補一個寫死的值** —— 那樣使用者在 Console 上設的預設 model 永遠到不了，
> 而「沒生效」在畫面上看起來跟「生效了」一模一樣（實際發生過，見 ZEN-012 的迴歸）。
> 常駐容器走工作項拿同一個欄位，**每筆 Run 重算**，所以中途改憑證下一筆就生效。

版本 validation 使用 `Authorization: Bearer <validation_token>`。token 綁定單一 `agent_version_id`，
TTL 最長 10 分鐘，validation 結束立即撤銷。A version token 存取 B version → `403 PERMISSION_DENIED`。
它不能存取 `/internal/runs/*`、`/internal/llm/*`、`/internal/egress/*` 或 `/internal/notify/*`。

**container token 的權限一樣要收死**：它只能打 `GET /internal/agents/{自己那個 agent}/work`。
打 `/internal/runs/*`、`/internal/llm/*`、`/internal/egress/*`、`/internal/notify/*` 一律
`403 PERMISSION_DENIED` —— 那些都要 run token，而 run token 是**跟著工作項**進來的，
一個 Run 一把。**出口與寄信都必須有 run token，稽核才綁得到 `run_id`**（§2.10）。

ZEN-000 用到的三條路由內容格式如下；這些是沙盒與平台間的契約，公開 `/v1` 的欄位與狀態不因骨架而縮減：

| 路由 | Request | 成功 Response |
|---|---|---|
| `GET /internal/runs/{run_id}/bootstrap` | 無 body | `200`：`{"run_id":"<uuid>","entrypoint":"default","input":{},"manifest":{},"default_model":"gpt-4o-mini"}` |
| `POST /internal/runs/{run_id}/events` | **頂層 JSON 陣列**：`[{"event_id":"01930000-0000-7000-8000-000000000003","type":"node.start","payload":{"node":"echo_node","ts":"..."}}]`；沙盒可送 `node.start`、`node.end`、`token`、`log`、`custom`、`error`，`run.status` 只由平台產生 | `202`：`{"accepted":1,"last_seq":42}`；`accepted` 是新寫入的事件數，`last_seq` 是此批最後一筆（含重送）的 SSE id，空陣列時為 `null` |
| `POST /internal/runs/{run_id}/result` | `{"output":{},"error":null,"usage":{}}`；失敗時 `output=null` 且 `error.code` 為 §0 枚舉值 | `202`：`{"accepted":true}` |

`POST /internal/versions/{agent_version_id}/validation` Request：

```json
{
  "valid": true,
  "runtime_contract": "zentra.image/v1",
  "manifest": {
    "apiVersion": "zentra/v1",
    "name": "research-agent",
    "version": "1.2.0"
  },
  "entrypoints": [{
    "name": "default",
    "description": "研究並摘要",
    "input_schema": {"type":"object","properties":{"question":{"type":"string"}},"required":["question"]},
    "output_schema": {"type":"object","properties":{"summary":{"type":"string"}},"required":["summary"]}
  }],
  "error": null
}
```

失敗時 `valid=false`、`manifest=null`、`entrypoints=null`，且 `error` 使用 §0 統一形狀並固定
`code=BUILD_FAILED`。成功 Response `202`：`{"accepted":true}`。此端點只保存 validation payload，
**不能自行把版本轉成 `PUBLISHED`**；build-worker 確認 validation container 以 0 結束、digest 未改變且結果存在後，
才唯一決定 `PUBLISHED` 或 `FAILED`。重送相同 payload 冪等；不同 payload 的第二次提交回 `422 VALIDATION_ERROR`。

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

### 4.1 設定與機密名字怎麼送進沙盒（v0.12.0 新增，ZEN-019）

**兩條派工路徑都要帶，只改一條等於沒改。** 常駐容器走的是工作項那條，而那是預設路徑。

```jsonc
// GET /internal/runs/{run_id}/bootstrap  →  200
{
  "run_id": "…", "entrypoint": "default", "input": {}, "manifest": {},
  "default_model": "gpt-4o-mini",
  "config": { "symbols": ["BTCUSDT"], "interval": "1h" },   // ← 新增，§2.6 原封不動
  "secret_names": ["SMTP_PASSWORD"]                          // ← 新增，排序後的名字，沒有值
}
```

```jsonc
// Redis 工作項（worker push → GET /internal/agents/{id}/work 回同一個物件）
{
  "run_id": "…", "run_token": "…", "entrypoint": "default", "input": {},
  "manifest": {}, "default_model": "gpt-4o-mini",
  "config": { … },            // ← 新增
  "secret_names": [ … ]        // ← 新增
}
```

**硬規則**：

1. **`config` 是頂層欄位，不得合併進 `manifest`。** `manifest` 是版本的不可變內容
   （validation 抽出、存進 `agent_versions.manifest`）；把可變設定攪進去，
   「manifest 不可變」在程式碼裡就不再成立，而那是 P5 在 runtime 這一側唯一的實體。
   SDK 的 `ctx.config` 要改成讀這個新欄位，**不再讀 `manifest["config"]`**。
2. **每筆 Run 重算。** 跟 `default_model` 同一條規則（見上一段）：中途改設定，**下一筆就生效**，
   已經在跑的不受影響。
3. **不准為了設定放寬沙盒 env 的精確集合檢查。**
   `apps/sandbox/drivers/base.py` 的 `_validate_environment()` 對每個 mode 做
   「多一個少一個都擋」，那是 P4 的第一道關。RUN 永遠恰好是
   `{ZENTRA_GATEWAY_URL, ZENTRA_RUN_ID, ZENTRA_RUN_TOKEN}`，
   SERVE 永遠恰好是 `{ZENTRA_GATEWAY_URL, ZENTRA_AGENT_ID, ZENTRA_CONTAINER_TOKEN,
   ZENTRA_MAX_CONCURRENT_RUNS}`。**這次不新增任何環境變數**（ADR-0014 選項 A）。
4. **validation 沙盒一律用 `config = {}` 與 `secret_names = []`，而且不新增任何路由**
   （v0.12.1 定案，U2 —— 這一點在 v0.12.0 寫反了，見 §5 變更紀錄與
   [ADR-0014 修訂 R1](./adr/ADR-0014-per-agent-config-secrets-and-egress.md)）。
   `validate` mode 的 `ZentraContext` 在容器內直接建構，**沒有**取 bootstrap 的動作，
   而 env 是精確集合（第 3 點）不能加變數 —— 要讓它拿到當下設定，只能發明一條契約外的
   internal 路由，那是契約漂移。

   **空 config 驗證不是將就，它比帶當下設定更嚴格**：契約已經要求
   **不要在 `build()` 裡對 config 做硬性要求**（用 `.get()` 帶預設值，或在節點裡讀），
   而用空 config 去 build 正好就是在驗這條規則。
   一個空 config 就 build 不起來的 agent，等使用者哪天清掉設定，
   **每一筆 Run 都會在 build 階段死掉而版本狀態仍是 `PUBLISHED`** ——
   那正是這條規則要防的事（ADR-0014 §7）。所以這個 agent **應該**驗證失敗。
   `validate` 失敗時的 `BUILD_FAILED` 訊息要看得出是 config 相關，
   那是使用者唯一的線索。

   同理，`ctx.secret_names` 在 validate 期間是空的：**不要在 `build()` 裡檢查機密是否齊全**，
   那個檢查要放在節點裡（範例見 ZEN-019 spec §5.5 的啟動自檢節點）。
5. `secret_names` **只有名字**：沒有值、沒有長度、沒有尾四碼。
   對應 SDK 能力是唯讀的 `ctx.secret_names`。**`ctx.secrets`（取值）不存在。**

### 4.2 `POST /internal/egress/request` — 對外 HTTP 代打（v0.12.0 新增，ZEN-019）

認證：`Authorization: Bearer <run_token>`。

```jsonc
// Request
{
  "url": "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1h&limit=200",
  "method": "GET",                       // 選填，預設 GET；只接受 GET | POST
  "headers": { "Accept": "application/json" },   // 選填，**只准 Accept 與 Content-Type**
  "body": null,                          // 選填，字串；只有 POST 能帶
  "timeout_seconds": 10                  // 選填，預設 10，上限 15
}
```

```jsonc
// 200 —— 請求真的送出去了。上游狀態碼在信封裡，不是 HTTP 層
{
  "status": 200,
  "headers": { "content-type": "application/json" },   // 只回傳安全的子集，見下
  "body": "[[1700000000000,\"42000.00\",…]]"
}
```

**硬規則**：

1. **信封語意**：只要請求真的送出去了，Gateway 一律回 `200`，上游的狀態碼放在 `status`。
   **上游回 418／429／500 不是平台錯誤。** 這是「Binance 主源、OKX 備援」能實作的前提 ——
   agent 要看得到狀態碼才切得了備援。Gateway 自己的錯誤才用 §0 的錯誤格式。
2. **平台注入認證，沙盒不得自帶。** `headers` 出現 `Accept` / `Content-Type` 以外的任何 key
   → **`422 VALIDATION_ERROR`**，`details.header` **指名是哪個 header**，不是默默丟掉。
   **這一條是 `422`，不是 `EGRESS_BLOCKED`**（v0.12.1 定案，U1）：自帶 header 是
   「這個請求本身寫錯了」，不是「一個格式正確的請求被政策擋下」。
   §4.3 的 reason 表因此**沒有** `HEADER_NOT_ALLOWED` 這一項。
   要帶認證，在 §2.8 的規則上設 `auth_secret_name` + `auth_header`，
   平台解密後加上去 —— 機密從頭到尾沒進過沙盒。
   平台固定送 `User-Agent: Zentra-Egress/1`。
   > **這個字串不是任意的，改之前要重驗主流交易所。** 實測：OKX 會擋
   > 字面 `Python-urllib/*` 這個 UA 並回 `403`（完全預設 → 403、只加 `Accept` → 403、
   > 只要換掉 UA 就 200）；`Zentra-Egress/1` 對 Binance 與 OKX 都回 `200`。
   > 有人覺得這字串不好看而改掉時，症狀會是**備援源突然全掛**，而且看起來像對方在擋我們。
3. **不跟隨 redirect。** 收到 3xx 直接回 `EGRESS_BLOCKED`（`reason: REDIRECT`），
   `details.location` 帶上目的地。跟隨的話，對方一行 `302` 就繞過白名單了。
4. **只准 `https`。** scheme 不是 `https` → `EGRESS_BLOCKED`（`reason: SCHEME_NOT_ALLOWED`）。
5. **解析結果落在 private / loopback / link-local / metadata 位址一律擋**
   （`reason: PRIVATE_ADDRESS`），含 `169.254.169.254`。這是 proxy 版本的 SPEC §5 硬規則 4：
   proxy 是從 `app` 發出去的，而 `app` 同時掛在 `zentra-net` 上。
6. **上限**：request body ≤ 64 KiB、response ≤ 1 MiB、逾時 ≤ 15 秒、
   每筆 Run ≤ `EGRESS_MAX_CALLS_PER_RUN`（預設 100）。
   **超量不截斷、不夾值，直接當錯誤** —— 截斷過的 JSON 解析失敗時，錯誤訊息會指向 agent 的
   解析程式碼而不是真正的原因。次數上限是為了不被交易所封 IP，而被封的是整台主機。
   - **`timeout_seconds > 15` 是 `422 VALIDATION_ERROR`，不是默默夾到 15**
     （v0.12.1 定案，U4）。與本點「超量不截斷」同一條原則：
     靜默夾值會讓「我設了 60 秒卻 15 秒就斷」變成查不出原因的怪事。
     `≤ 0` 或非整數同樣 `422`。
   - **次數上限計的是「通過認證的嘗試」，被擋下的也算**（v0.12.1 定案，U5）。
     只計成功的話，一個一直打非白名單主機的迴圈就完全不受限 ——
     而**會失控的迴圈通常正是一直失敗的那個**，那才是這條上限要擋的東西。
     計數來源就是 `egress_calls` 裡這筆 Run 的 `kind='HTTP'` 列數，
     **不另外維護計數器**：兩份會不一致，而不一致時沒有辦法判斷哪份是對的。
7. **回傳的 `headers` 是白名單子集**：`content-type`、`content-length`、`date`、
   `retry-after`、`x-ratelimit-*`。`set-cookie` 與 `authorization` 一律不回傳。
8. **每一個通過認證的請求都寫一列 `egress_calls`，`422` 與被擋下的都要寫**
   （v0.12.1 定案，U3；欄位語意見 §2.10 第 4 點）。
   稽核要回答的第一個問題是「**為什麼我的 agent 連不出去**」，而那條路徑上一列都沒有
   才是最糟的情況。**唯一的例外是連認證都沒過**（`401`／`403` token 無效）：
   那時沒有可信的 `run_id`，而 `egress_calls.run_id` 是 `NOT NULL`。
   這條界線也是實作上的判準：**驗完 token 之後的每一條 return 路徑都要先寫稽核。**
9. **沒有任何旗標或環境變數可以關掉第 3、4、5 點**（v0.12.1 定案，U8）。
   特別是第 5 點的私有位址檢查：`apps/**` 裡**不得**存在
   「允許連內網」「跳過解析檢查」之類的開關，連預設關閉的都不行 ——
   一個存在的旁路遲早會有人打開，而它打開時沒有任何症狀。
   怎麼在「不准連內網」的前提下測到成功路徑，見 ZEN-019 spec §5.3.1，
   那是測試策略，不是契約的例外。

### 4.3 `details.reason` 枚舉（`EGRESS_BLOCKED` 專用，v0.12.0 新增）

錯誤 code 是公開契約、發布後要長期相容；**會成長的那一維放在 `details.reason`**，
之後加原因時不用改 §0 的枚舉。

**這張表只服務 `EGRESS_BLOCKED`（403）。** `422 VALIDATION_ERROR` **不用 `reason`** ——
它用 §0 既有的欄位層級 `details`（例如 `details.header`、`details.field`）。
把兩種錯誤混進同一個枚舉，會讓「這個請求該改寫，還是該去改設定」分不出來，
而那正是呼叫端唯一需要判斷的事。

| reason | 出現在 | 意思 |
|---|---|---|
| `NO_RULES` | HTTP | 這個 Agent 一條白名單規則都沒有 |
| `HOST_NOT_ALLOWED` | HTTP | 主機不在白名單 |
| `METHOD_NOT_ALLOWED` | HTTP | 該規則沒開這個 method |
| `PATH_NOT_ALLOWED` | HTTP | 不符合該規則的 `path_prefix` |
| `SCHEME_NOT_ALLOWED` | HTTP | 不是 `https` |
| `PRIVATE_ADDRESS` | HTTP | 解析到內網／loopback／link-local |
| `REDIRECT` | HTTP | 上游回 3xx（`details.location`） |
| `REQUEST_TOO_LARGE` / `RESPONSE_TOO_LARGE` | HTTP | 超過 §4.2 第 6 點的上限 |
| `RUN_CALL_LIMIT` | HTTP | 超過 `EGRESS_MAX_CALLS_PER_RUN` |
| `SENDER_NOT_CONFIGURED` | EMAIL | 這個 Agent 沒有 §2.9 的寄件者設定 |
| `RECIPIENT_NOT_ALLOWED` | EMAIL | 收件人不在 `allowed_recipients`（`details.recipient` 指名） |
| `BODY_TOO_LARGE` | EMAIL | 信件內容超過上限 |
| `RUN_EMAIL_LIMIT` / `HOURLY_EMAIL_LIMIT` | EMAIL | 超過每 Run／每小時上限 |
| `SMTP_AUTH` | EMAIL | SMTP 認證失敗。**歸在 blocked 不是 failed**：密碼錯了重試一百次還是錯，而且 Gmail 會因此鎖帳號 |
| `TLS_REQUIRED` | EMAIL | 連上了但升不上 TLS |

### 4.4 `POST /internal/notify/email` — 寄信（v0.12.0 新增，ZEN-019）

認證：`Authorization: Bearer <run_token>`。SMTP 不是 HTTP，**不走 §4.2**。

```jsonc
// Request
{
  "to": ["me@example.com"],          // 必填，1–10 個，全部要在 allowed_recipients 裡
  "subject": "BTCUSDT 出現看跌蝙蝠型態",   // 必填，≤ 200 字元
  "text": "RSI 72.4 / D 點 0.886 回撤…",  // text 與 html 至少要有一個
  "html": null
}
```

```jsonc
// 202
{ "accepted": true, "message_id": "<…@zentra>" }
```

**硬規則**：

1. **寄件者、帳號、密碼都由平台決定**（§2.9）。request 不能指定 `from`、
   不能指定 SMTP 主機、不能帶密碼 —— 出現這些欄位回 `422`。
2. **收件人逐一比對 `allowed_recipients`**，有任何一個不在清單裡，
   **整封信都不寄**並回 `EGRESS_BLOCKED`（`reason: RECIPIENT_NOT_ALLOWED`）。
   部分寄出會讓「到底寄了沒」說不清楚。
3. **上限**：`text` + `html` 合計 ≤ 256 KiB；每筆 Run ≤ `EMAIL_MAX_PER_RUN`（預設 5）；
   每個 Agent 每小時 ≤ `EMAIL_MAX_PER_HOUR`（預設 60）。
   訊號型 agent 最典型的 bug 就是重試迴圈裡寄信。
   - **每小時是滾動 60 分鐘窗口，不是整點桶**（v0.12.1 定案，U6）。
     整點桶允許 `:59` 寄 60 封、`:01` 再寄 60 封 —— 兩分鐘 120 封，
     那正是這條上限存在的理由。
   - 計數就是一句
     `SELECT count(*) FROM egress_calls WHERE agent_id = ? AND kind = 'EMAIL'
     AND created_at > now() - interval '1 hour'`。
     **不另外維護計數器**（同 §4.2 第 6 點：兩份會不一致）。
     `(agent_id, created_at DESC)` 索引已經在，每小時 ≤ 60 的量級下這個 COUNT 不值一提。
   - **兩個上限計的都是「嘗試」，被擋下的也算**，包含 `SMTP_AUTH` 失敗。
     理由很具體：**反覆認證失敗正是 Gmail 鎖帳號的主因之一**，
     那種情況要的是停下來，不是「因為沒寄成功所以可以再試」。
4. **不附檔、不排程、不做退信處理。** Gmail + 應用程式密碼是給「寄給自己」用的。
5. **`202` 表示 SMTP 伺服器收下了，不表示對方讀到了。** 文案與 SDK docstring 都要這樣寫。
6. **不冪等。** 重送就是再寄一封。要冪等由 agent 自己在 `ctx.config` 裡記狀態 ——
   平台做冪等要定義「什麼算同一封信」，那個定義一定會有人不同意。
7. 每一次寄信寫一列 `egress_calls`（`kind: EMAIL`），**被擋下的也要寫**。

---

## 5. 契約變更紀錄

| 版本 | 日期 | 變更 | 由 |
|---|---|---|---|
| v0.13.0 | 2026-10-15 | 依 [ADR-0016](./adr/ADR-0016-same-semver-replaces-version.md)：同版號重新註冊改為取代（§2 版本段落）；`VERSION_CONFLICT` 在版本註冊上只表示「同版號正在驗證中」；`GET /versions/{semver}` 明訂回最近一次註冊。**狀態枚舉對外不變**（`SUPERSEDED` 不會出現在任何回應） | 使用者裁示 |
| v0.12.1 | 2026-09-30 | **收斂 ZEN-019 驗收時挖出的九處矛盾／未定義**（@reviewer 提，U1–U9）。**無新端點、無新欄位、無新錯誤 code。** **U1** 沙盒自帶 header 是 `422 VALIDATION_ERROR`（`details.header` 指名），**`HEADER_NOT_ALLOWED` 從 §4.3 移除** —— 那張表只服務 `EGRESS_BLOCKED`（403）。**U2 ⚠️ 行為反轉**：§4.1 第 4 點原本要求 validation 沙盒帶當下 `config`／`secret_names`，但沒有、也不該有那條路由；改為**一律 `config={}`、`secret_names=[]`**，並說明空 config 驗證比帶設定更嚴格（見 [ADR-0014 修訂 R1](./adr/ADR-0014-per-agent-config-secrets-and-egress.md)）。**U3** 通過認證後的每一條 return 路徑都寫 `egress_calls`，含 `422` 與被擋下的；唯一例外是認證未過（無可信 `run_id`），§2.10 第 4 點給出 `error`／`status_code`／`rule_id` 的四種組合。**U4** `timeout_seconds > 15` 是 `422`，不夾值。**U5** 次數上限計「通過認證的嘗試」，被擋下的也算，計數來源就是 `egress_calls` 不另設計數器。**U6** 寄信每小時上限是**滾動 60 分鐘窗口**（整點桶會允許兩分鐘 120 封），一樣計嘗試，含 `SMTP_AUTH` 失敗。**U7** `last_four` 在值長度 < 4 時是 `*` 重複該長度次（寫死既有行為）。**U8** 明定**不得存在**關閉私有位址檢查的旗標或環境變數，測試策略改走 ZEN-019 spec §5.3.1（純函式化的位址判斷 + 可注入 resolver／transport；`ZENTRA_TEST_ALLOW_PUBLIC_EGRESS` 只被 `tests/**` 讀，`apps/**` 零命中要有護欄測試）。**U9** `PUT /secrets/{name}` 新建與覆寫都回 `200`。另補 §4.2 第 2 點的 `User-Agent: Zentra-Egress/1` 註解（實測 OKX 擋 `Python-urllib/*`，改這個字串會讓備援源全掛） | @architect |
| v0.12.0 | 2026-09-30 | **新增 per-agent 設定／機密／對外能力三層**（[ADR-0014](./adr/ADR-0014-per-agent-config-secrets-and-egress.md)，ZEN-019）。公開端點：`GET`/`PUT /v1/agents/{id}/config`（§2.6）、`GET /secrets` + `PUT`/`DELETE /secrets/{name}`（§2.7）、`GET`/`POST /egress-rules` + `PUT`/`DELETE /egress-rules/{rule_id}`（§2.8）、`GET`/`PUT`/`DELETE /email-sender`（§2.9）、`GET /egress-calls`（§2.10）。Internal Gateway：bootstrap 與工作項**兩條路都新增 `config` 與 `secret_names` 欄位**（§4.1），新增 `POST /internal/egress/request`（§4.2）與 `POST /internal/notify/email`（§4.4），新增 `details.reason` 枚舉（§4.3）。錯誤 code：`EGRESS_BLOCKED` 語意擴充為「出口請求被政策擋下」且指定 HTTP `403`，**新增 `EGRESS_FAILED`（502, retryable）**。**Runs／SSE／MCP／版本發布的形狀完全未變**；沙盒 env 的精確集合檢查**未放寬**（§4.1 第 3 點） | @architect |
| v0.11.0 | 2026-09-29 | **`DELETE /v1/agents/{id}` 改為硬刪除**，一併清掉容器與 registry image（[ADR-0013](./adr/ADR-0013-hard-delete-agents-and-images.md)），見 §2.5。`GET /v1/runs` 的每一列新增 `agent_name`（只有 agent_id 的話列表得人肉比對 UUID） | @architect |
| v0.10.0 | 2026-09-28 | **並發溢出**（ADR-0012 選項 C 的後半）：容器全滿時背景加開下一個，上限 `SANDBOX_MAX_CONTAINERS_PER_AGENT`（預設 3）；§2.2 第 5 點。`inflight_runs` 保證不超過 `max_concurrent_runs`。**端點與回應形狀未變** | @architect |
| v0.9.0 | 2026-09-28 | **常駐容器改為隨第一筆 Run 自動啟動、閒置自動停止**（§2.2）；`POST /containers` 語意變成預熱。Internal Gateway 的 bootstrap 新增 `default_model`（§4），讓 per-agent 預設 model 真的送得到沙盒。**公開端點的形狀未變** | @architect |
| v0.8.0 | 2026-09-28 | **新增用量與除錯查詢**（ZEN-015 §3.3）：`GET /v1/agents/{id}/usage`、`GET /v1/agents/{id}/llm-calls` 與 §2.4。`GET /v1/runs` 新增 `api_key_id` 篩選。**既有回應形狀未變**，無新增錯誤 code | @architect |
| v0.7.0 | 2026-09-28 | **新增 per-agent 下游 API key**（ZEN-015）：`POST`/`GET`/`DELETE /v1/agents/{id}/api-keys` 與 §2.3。認證是**有帶就驗、沒帶放行**（`ZENTRA_REQUIRE_AGENT_API_KEY` 可改成強制），REST 與 MCP 同一套規則。`runs` 新增 `api_key_id`，**回應形狀未變**，無新增錯誤 code | @architect |
| v0.6.0 | 2026-09-27 | **新增常駐容器端點**（ZEN-015 / ADR-0012）：`GET`/`POST /v1/agents/{id}/containers`、`POST .../restart`、`DELETE .../{cid}`，與 §2.2。新增錯誤 code `AGENT_NOT_RUNNING`。**Runs／SSE／MCP 的形狀完全未變** —— 常駐化對呼叫端無感 | @architect |
| v0.5.0 | 2026-09-26 | **新增 per-agent LLM 憑證**（ZEN-012）：`PUT`/`GET`/`DELETE /v1/agents/{id}/llm-credential` 三條端點與 §2.1。憑證綁 Agent 不綁版本；回應永不含金鑰。Runs／SSE／MCP 形狀未變，無新增錯誤 code | @architect |
| v0.4.1 | 2026-09-23 | 依 [ADR-0009 修訂 R2](./adr/ADR-0009-internal-registry-image-upload.md) **撤回 v0.4.0 的全部新增**：移除 multipart 形狀、移除 `artifact.origin`、`version_id`/`artifact.image_ref`/`artifact.digest` 恢復為任何狀態都非 `null`、`details.stage` 值域退回 `PULL|INSPECT|VALIDATE`、`QUOTA_EXCEEDED` 退回只表示併發 Run 上限。改為使用者在平台主機上直接 `docker push localhost:6000/<repo>:<tag>` 後以既有 JSON 路徑註冊。**v0.4.0 的新增從未實作，因此相對於 v0.3.1 本次是零變更** | @architect |
| v0.4.0 | 2026-09-22 | 依 [ADR-0009](./adr/ADR-0009-internal-registry-image-upload.md) 讓 `POST /v1/agents/{id}/versions` 同時接受 multipart image tar 上傳；新增 `artifact.origin`（純描述性）；`version_id`/`artifact.image_ref`/`artifact.digest` 在 `UPLOADED` + `BUILDING` 期間可為 `null`；`details.stage` 值域加 `LOAD`/`PUSH`；`QUOTA_EXCEEDED` 說明放寬到含磁碟餘裕。**沒有新增錯誤 code**，`version_id` 語意與 Runs/SSE/MCP 形狀未變 | @architect |
| v0.3.1 | 2026-09-22 | 將 `trigger` 收斂為 server-known ingress protocol：REST/Console=`API`、MCP=`MCP`；移除 `CONSOLE`，禁止 body/header discriminator。端點與 Run 建立流程未新增 | @architect |
| v0.3.0 | 2026-09-22 | 依 ADR-0008 將 Agent Version 發布改為 JSON 註冊 canonical OCI digest；新增版本 artifact/source 欄位、Agent list/create 具體 shape 與 validation token/body。Runs、SSE、MCP 對外形狀不變 | @architect |
| v0.2.2 | 2026-09-21 | 明定 `GET Run` 未產生欄位的 null 語意、無 LLM 用量；§4 補上 bootstrap/events/result schema、事件 `event_id` 冪等與 worker 唯一決定終態；澄清 `/internal/*` 可由主機埠到達且必驗 token。公開端點和枚舉未新增 | @architect |
| v0.1.0 | 2026-09-20 | 初版：Runs / Agents / MCP / 錯誤枚舉 | @architect |
| v0.2.1 | 2026-09-20 | Console 改為同源消費本契約（[ADR-0006](./adr/ADR-0006-vanilla-frontend.md)）：§0 補上同源 / 認證 / 靜態檔路由說明。**端點、欄位、枚舉全部未變** | @architect |
| v0.2.0 | 2026-09-20 | **降維改版**（ADR-0005）：移除 Tools / Knowledge 端點群、`runs/{id}/resume`、alias 切換、`config.tier`、MCP `resources/*`；狀態枚舉移除 `WAITING_INPUT`；事件枚舉移除 `tool.*` / `interrupt`，新增 `log` / `custom`；錯誤碼移除 `TOOL_ERROR`，新增 `LLM_ERROR` / `BUILD_FAILED`；版本上傳改為 multipart 直傳 + `version_id` = bundle SHA-256；新增 §4 Internal Gateway | @architect |
