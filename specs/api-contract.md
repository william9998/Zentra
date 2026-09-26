# API 契約 (API Contract)

| 欄位 | 值 |
|---|---|
| 契約版本 | `v0.5.0` |
| 擁有者 | `@architect` |
| 對齊 Spec | `specs/SPEC.md v0.4.1` |

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
| `VERSION_CONFLICT` | 409 | false | 同一 Agent 的 semver 已存在，P5 禁止覆寫 |
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
| `POST` | `/v1/agents/{id}/versions` | **註冊 OCI image digest 並發布**（見下） |
| `GET` | `/v1/agents/{id}/versions/{semver}` | 版本詳情，含 `entrypoints` 與 build 錯誤 |
| `GET` | `/v1/agents/{id}/openapi.json` | 該 Agent 的 OpenAPI 3.1 schema（P3） |
| `PUT` | `/v1/agents/{id}/llm-credential` | 註冊／覆寫這個 Agent 的 LLM 憑證（見 §2.1） |
| `GET` | `/v1/agents/{id}/llm-credential` | 憑證的 metadata。**永遠不含金鑰** |
| `DELETE` | `/v1/agents/{id}/llm-credential` | 移除憑證，退回用平台預設金鑰 |

> `PUT /v1/agents/{id}/aliases/{alias}`（流量切換）於 v0.2.0 移除。
> 呼叫時不指定 `version` 即用最新 `PUBLISHED` 版本。

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
`BUILD_FAILED` 一律 `retryable:false`：重試必須以**新的 `semver`** 重新送出。

同一個 `agent_id + semver` 重複註冊 → `409 VERSION_CONFLICT`，不得建立第二列。
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
| `GET` | `/internal/runs/{run_id}/bootstrap` | 取 manifest、entrypoint 名稱、input |
| `POST` | `/internal/runs/{run_id}/events` | 回寫事件（批次，body 是事件陣列） |
| `POST` | `/internal/runs/{run_id}/result` | 回寫最終 output 或 error |
| `POST` | `/internal/llm/v1/chat/completions` | **OpenAI 相容**。平台換上真金鑰、計量 token、寫 `llm_calls` |
| `POST` | `/internal/versions/{version_id}/validation` | `--mode=validate` 沙盒回報驗證結果與抽出的 entrypoints schema |

版本 validation 使用 `Authorization: Bearer <validation_token>`。token 綁定單一 `agent_version_id`，
TTL 最長 10 分鐘，validation 結束立即撤銷。A version token 存取 B version → `403 PERMISSION_DENIED`。
它不能存取 `/internal/runs/*` 或 `/internal/llm/*`。

ZEN-000 用到的三條路由內容格式如下；這些是沙盒與平台間的契約，公開 `/v1` 的欄位與狀態不因骨架而縮減：

| 路由 | Request | 成功 Response |
|---|---|---|
| `GET /internal/runs/{run_id}/bootstrap` | 無 body | `200`：`{"run_id":"<uuid>","entrypoint":"default","input":{},"manifest":{}}` |
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

---

## 5. 契約變更紀錄

| 版本 | 日期 | 變更 | 由 |
|---|---|---|---|
| v0.5.0 | 2026-09-26 | **新增 per-agent LLM 憑證**（ZEN-012）：`PUT`/`GET`/`DELETE /v1/agents/{id}/llm-credential` 三條端點與 §2.1。憑證綁 Agent 不綁版本；回應永不含金鑰。Runs／SSE／MCP 形狀未變，無新增錯誤 code | @architect |
| v0.4.1 | 2026-09-23 | 依 [ADR-0009 修訂 R2](./adr/ADR-0009-internal-registry-image-upload.md) **撤回 v0.4.0 的全部新增**：移除 multipart 形狀、移除 `artifact.origin`、`version_id`/`artifact.image_ref`/`artifact.digest` 恢復為任何狀態都非 `null`、`details.stage` 值域退回 `PULL|INSPECT|VALIDATE`、`QUOTA_EXCEEDED` 退回只表示併發 Run 上限。改為使用者在平台主機上直接 `docker push localhost:6000/<repo>:<tag>` 後以既有 JSON 路徑註冊。**v0.4.0 的新增從未實作，因此相對於 v0.3.1 本次是零變更** | @architect |
| v0.4.0 | 2026-09-22 | 依 [ADR-0009](./adr/ADR-0009-internal-registry-image-upload.md) 讓 `POST /v1/agents/{id}/versions` 同時接受 multipart image tar 上傳；新增 `artifact.origin`（純描述性）；`version_id`/`artifact.image_ref`/`artifact.digest` 在 `UPLOADED` + `BUILDING` 期間可為 `null`；`details.stage` 值域加 `LOAD`/`PUSH`；`QUOTA_EXCEEDED` 說明放寬到含磁碟餘裕。**沒有新增錯誤 code**，`version_id` 語意與 Runs/SSE/MCP 形狀未變 | @architect |
| v0.3.1 | 2026-09-22 | 將 `trigger` 收斂為 server-known ingress protocol：REST/Console=`API`、MCP=`MCP`；移除 `CONSOLE`，禁止 body/header discriminator。端點與 Run 建立流程未新增 | @architect |
| v0.3.0 | 2026-09-22 | 依 ADR-0008 將 Agent Version 發布改為 JSON 註冊 canonical OCI digest；新增版本 artifact/source 欄位、Agent list/create 具體 shape 與 validation token/body。Runs、SSE、MCP 對外形狀不變 | @architect |
| v0.2.2 | 2026-09-21 | 明定 `GET Run` 未產生欄位的 null 語意、無 LLM 用量；§4 補上 bootstrap/events/result schema、事件 `event_id` 冪等與 worker 唯一決定終態；澄清 `/internal/*` 可由主機埠到達且必驗 token。公開端點和枚舉未新增 | @architect |
| v0.1.0 | 2026-09-20 | 初版：Runs / Agents / MCP / 錯誤枚舉 | @architect |
| v0.2.1 | 2026-09-20 | Console 改為同源消費本契約（[ADR-0006](./adr/ADR-0006-vanilla-frontend.md)）：§0 補上同源 / 認證 / 靜態檔路由說明。**端點、欄位、枚舉全部未變** | @architect |
| v0.2.0 | 2026-09-20 | **降維改版**（ADR-0005）：移除 Tools / Knowledge 端點群、`runs/{id}/resume`、alias 切換、`config.tier`、MCP `resources/*`；狀態枚舉移除 `WAITING_INPUT`；事件枚舉移除 `tool.*` / `interrupt`，新增 `log` / `custom`；錯誤碼移除 `TOOL_ERROR`，新增 `LLM_ERROR` / `BUILD_FAILED`；版本上傳改為 multipart 直傳 + `version_id` = bundle SHA-256；新增 §4 Internal Gateway | @architect |
