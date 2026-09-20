# Zentra Agent Platform — 主規格書 (Master Spec)

> **這份文件是唯一事實來源 (Single Source of Truth)。**
> 所有 agent（架構師 / UI / 前端 / 後端 / QA）在開工前必須先讀這份文件。
> 任何與本文件衝突的程式碼、設計稿、測試，一律以本文件為準；
> 若發現本文件有錯，**先改這份文件**（走 §12 變更流程），再改程式碼。

| 欄位 | 值 |
|---|---|
| Spec 版本 | `v0.2.1` |
| 狀態 | `DRAFT` |
| 最後更新 | 2026-09-20 |
| 擁有者 | `@architect` |

> **v0.2.x 是一次降維改版。** 依 [ADR-0005](./adr/ADR-0005-mvp-single-host-scope.md)：
> 部署從 Kubernetes 收斂成**單機 docker compose**，分層從 L0–L6 七層降到**四層**，範圍收斂成**一條主線**。
> 再依 [ADR-0006](./adr/ADR-0006-vanilla-frontend.md)：前端改用**原生 HTML + CSS + JS（零建置）**，
> `console` 服務取消，由 `api` 直接服務靜態檔 —— 基礎設施從 11 個服務降到 **4 個**。
> **五條核心立場 P1–P5 一條都沒鬆** —— 降的是實作維度，不是立場。

---

## 1. 產品一句話

**Zentra 是一個 Agent 平台**：開發者上傳自己的 Python 程式碼（LangGraph），
平台把它凍結成不可變版本、在**隔離沙盒**中執行，並自動對外開放 **REST API** 與 **MCP** 兩種接口。

### 1.1 交付條件（決定了下面所有取捨）

| 條件 | 值 |
|---|---|
| 使用場景 | 內部工具 |
| 租戶 | 單一租戶 |
| 程式碼來源 | **只跑自己信得過的程式碼** |
| 交付目標 | MVP demo |
| 時程 | 2 個月（8 週） |
| 部署 | **單機 docker compose**（4 個服務），本機與正式是同一套 |

**這張表變了，整份規格就要重讀。** 尤其「信任的程式碼」這條——
一旦要跑不是自己寫的 Agent，回去讀 [ADR-0001](./adr/ADR-0001-sandbox-isolation.md) 與 [ADR-0005](./adr/ADR-0005-mvp-single-host-scope.md) 的「何時該重新檢討」。

## 2. 核心設計立場（不可妥協的五條）

| # | 立場 | 理由 | 在 MVP 怎麼成立 |
|---|---|---|---|
| P1 | **使用者程式碼永遠不在平台行程內執行** | 這是整個平台的安全邊界，也是這個產品的核心技術 | Celery worker 只呼叫 Docker API。連「檢查 `build()` 是否存在」都在一次性沙盒裡做——`import` 使用者模組就是執行使用者程式碼 |
| P2 | **Agent 框架統一 LangChain + LangGraph** | 收斂成一種，串流 / 觀測 / 之後的 checkpoint 才只要寫一次 | 使用者交出未 compile 的 `StateGraph`，平台負責 compile |
| P3 | **每個 Agent 天生就有 API + MCP 兩個門面** | 不是額外功能，是出廠預設 | 同一份 `entrypoints` 生成 OpenAPI schema 與 MCP tool schema，MCP 掛在同一個 FastAPI app |
| P4 | **沙盒內拿不到任何長期憑證** | 即使沙盒被攻陷，損失也要可控 | 沙盒網段 `internal: true`，唯一對端是 **Internal Gateway**；只拿綁定單一 `run_id`、TTL = run timeout 的 **run token** |
| P5 | **Agent 版本不可變 (Immutable)** | 發布過的東西不能在腳下被換掉 | `version_id` = bundle 的 SHA-256。發布後 bundle 目錄與 venv 目錄設唯讀 |

## 3. 名詞定義

完整詞彙表見 [`specs/glossary.md`](./glossary.md)。最關鍵的六個：

- **Agent**：一個可被呼叫的智能體定義，由 `manifest` + `code bundle` 構成。
- **Agent Version**：Agent 的不可變快照，`agent_id@semver`，以 bundle SHA-256 識別。執行的最小單位。
- **Run**：一次 Agent 執行實例，有 `run_id`、狀態機（§9）、事件流。
- **Entrypoint**：Agent 對外暴露的一個可呼叫入口。**同一份定義同時生成 REST 與 MCP schema**（P3 的實作）。
- **Sandbox**：執行 Agent 程式碼的隔離容器，**一 Run 一盒，結束即銷毀，絕不重用**。
- **Internal Gateway**：`api` 服務上的一組 `/internal/*` 路由，沙盒**唯一**的網路對端。代持 LLM 憑證、收事件、發 input。

## 4. 使用者與使用情境

| 角色 | 情境 | 主要介面 |
|---|---|---|
| **Agent 開發者** | 寫 Python（LangGraph）、`zentra deploy` 上傳、發布新版本 | CLI + SDK |
| **外部系統** | 以 REST 或 MCP 呼叫已發布的 Agent | `/v1/...` · `/mcp/agents/{id}` |
| **維運者（＝同一個人）** | 看 Run 的事件流、失敗原因、token 用量 | Console |

MVP 沒有「平台組裝者」與「平台管理員」這兩個角色——它們對應的功能在 §11 的 MVP 外。

## 5. 系統分層（權威定義）

架構圖見 [`docs/architecture/detailed-architecture.md`](../docs/architecture/detailed-architecture.md)。

```
L0 接入 + 控制   api (FastAPI)  REST · MCP · SSE · Console 靜態檔 · Internal Gateway · Agent Registry
L1 派工          worker (Celery, Redis broker)  q.run · q.build
L2 執行          Sandbox Manager → Sandbox（Docker，一 Run 一盒）
L3 資料          PostgreSQL · Redis · 本機檔案（bundles / venvs）
```

> **沒有 Console BFF。** Console 是原生 HTML/CSS/JS 靜態檔，由 `api` 服務，
> 與 API **同源**，直接消費 `/v1` —— 它是 `api-contract.md` 的第一個真實消費者（[ADR-0006](./adr/ADR-0006-vanilla-frontend.md)）。

觀測**不是一層**，是橫切：結構化 JSON 日誌 + `run_events` 表 + `llm_calls` 表。

**分層硬規則**：
1. 上層只能呼叫下一層或同層的公開介面。
2. **L2 沙盒不得直連 L3 資料層**——沒有 Postgres 連線、沒有 Redis 連線，一律經 Internal Gateway。
3. 沙盒的網路對端**有且只有一個**：`api`。

## 6. 技術選型（已定案，變更需開 ADR）

| 範疇 | 選型 | 版本 | 備註 |
|---|---|---|---|
| 後端語言 | Python | 3.12 | |
| Web 框架 | FastAPI + Pydantic v2 | 0.115+ | 全 async |
| ORM / Migration | SQLAlchemy 2.0 + Alembic | | |
| 派工 | Celery | 5.4 | **Broker 與 Result 都用 Redis**（不用 RabbitMQ） |
| Agent 框架 | LangChain + LangGraph | langchain 0.3 / langgraph 0.2+ | **唯一允許** |
| 沙盒 | **Docker**（`DockerSandboxDriver`） | | 四層隔離，**無 gVisor**，見 §6.2 |
| MCP | FastMCP（Streamable HTTP） | | 掛載在同一個 FastAPI app |
| 資料庫 | PostgreSQL | 16 | 用 `pgvector/pgvector:pg16` 映像——擴充先備著，MVP 不用 |
| 快取 / 串流 | Redis | 7 | Celery broker + result + Streams 事件匯流 |
| 物件儲存 | **本機 volume** | | `data/bundles/`、`data/venvs/`，走 `ObjectStore` 介面，之後可換 S3 |
| 套件安裝 | uv | | per-version venv，**不建 per-agent image** |
| 前端 | **原生 HTML + CSS + ES modules** | — | **零建置**：無 bundler、無 TypeScript、無 npm / pnpm（[ADR-0006](./adr/ADR-0006-vanilla-frontend.md)） |
| UI | 原生 CSS custom properties | | 設計 token 見 [`specs/ui-contract.md`](./ui-contract.md)，**本來就是純 CSS 變數** |
| 前端狀態 | 一個 `store.js`（發布／訂閱，約 40 行） | | 不引入任何執行期函式庫 |
| 串流 | 原生 `EventSource` | | SSE 是瀏覽器原生能力 |
| 測試 | pytest / Playwright | | **無 Vitest**；Playwright 測瀏覽器，跟框架無關 |
| 觀測 | 結構化 JSON log + DB 事件表 | | 不引入 OTel / Langfuse / Prometheus |
| 套件管理 | uv（Python）**only** | | 前端沒有套件管理員，因為沒有套件 |

### 6.1 執行環境（4 個長駐服務）

```
docker compose（長駐）                    Sandbox Manager 動態建立（不在 compose 檔裡）
  postgres                                  sandbox-<run_id>
  redis                                     每 Run 一個，結束即銷毀
  api      ← L0（含 Console 靜態檔）
  worker   ← L1
```

**Console 沒有自己的容器**：`api` 用 `StaticFiles` 掛載 `apps/console/`，
並以五條 `FileResponse` 路由給出乾淨網址（`/console/agents`、`/console/runs/{run_id}` …），
`/` 轉址到 `/console/agents`。**沒有前端路由器** —— 深層連結與上一頁由伺服器處理。

**沙盒為什麼不寫在 compose 裡**：compose 宣告的是「開機就跑、一直在那」的長駐服務；
沙盒是「使用者打一次 API 才開一個、跑完就刪」的動態資源。這是程式邏輯，YAML 表達不了。

### 6.2 沙盒隔離（四層，核心技術）

| 層 | 措施 | 驗收方式 |
|---|---|---|
| **L1 網路** | 沙盒網段 `internal: true`（無對外路由）；唯一對端是 `api` 的 Internal Gateway | 沙盒內 curl `1.1.1.1`、`169.254.169.254`、`postgres:5432` **必須全部失敗** |
| **L2 核心** | `cap_drop ALL`、`no-new-privileges`、non-root `65534:65534`、Docker 預設 seccomp。**無 gVisor** | `mount -t proc`、`unshare`、`ptrace` **必須失敗**；`id` 必須非 root |
| **L3 資源** | `mem_limit` 2g、`pids_limit` 256、`nano_cpus` 2 core、無 swap、wall-clock timeout、stdout 上限 10MB | fork bomb、記憶體炸彈、無窮迴圈**必須被殺掉且不影響鄰居** |
| **L4 檔案** | `read_only=True` rootfs；`/app/code`、`/app/venv` 唯讀 bind；`/workspace` tmpfs 512m；**無 hostPath、無 docker.sock** | 寫 `/`、`/app` **必須失敗**；`/workspace` 可寫且隨盒銷毀 |
| **L5 應用** | SDK 攔截層：禁自建 LLM client、禁裸 socket | 沙盒內 `import socket` 直連外網**必須被擋** |

**缺的是 L2 的核心隔離（gVisor）。** `SandboxDriver.capabilities()` 必須回報 `l2_kernel_isolation: false`，
安全測試對應項標 **`skipped`，絕不標 `passed`**。

**沒有全部驗證過，不准說沙盒做好了。** 每一項右欄都要有對應的自動化測試。

## 7. 核心流程（權威版本，時序圖見架構文件）

### 7.1 發布一個 Agent 版本

1. CLI `zentra deploy` 打包 `./` → `POST /v1/agents/{id}/versions` 上傳 `.tar.gz`
2. `api` 落檔、寫 `agent_versions`（`BUILDING`）、送 Celery task 進 `q.build`
3. build worker：解壓 → 算 SHA-256 得 `version_id` → `uv pip install --target data/venvs/<version_id>`
   （**在一次性容器內跑**，因為 `setup.py` 等同任意程式碼）
4. 起一個 `--mode=validate` 沙盒：`import` 使用者模組，驗證 export 了符合 §8.1 契約的 `build()`，
   抽出 `entrypoints` 的 input/output schema，結果 POST 回 Internal Gateway
5. 通過 → bundle 與 venv 目錄設唯讀（P5）→ `PUBLISHED`，配發 REST 路由與 MCP 端點（P3）
6. 失敗 → `FAILED` + 具體原因寫進 `agent_versions.error`

### 7.2 呼叫 Agent（非同步 Run）

1. `POST /v1/agents/{id}/runs` → `api` 驗證 input schema → 寫 `runs`（`QUEUED`）→ 回 `202 { run_id }`
2. 送 Celery task 進 `q.run`
3. worker 取件（`acks_late=True` + 冪等）→ 向 Sandbox Manager 申請沙盒
   → **worker 本身不執行使用者程式碼（P1）**
4. Sandbox Manager 建立容器：唯讀掛 bundle 與 venv、注入 `ZENTRA_RUN_TOKEN`、接上沙盒網段
5. 沙盒內 Agent Runtime：`import build()` → 平台 compile（注入事件 hook）→ 逐節點執行
6. 每個事件 `POST /internal/runs/{run_id}/events` → `api` 寫 `run_events` 表 + `XADD` Redis Stream
7. 呼叫者 `GET /v1/runs/{id}/events`（SSE，支援 `Last-Event-ID` 續傳）或輪詢 `GET /v1/runs/{id}`
8. 結束 → 寫結果與用量 → 撤銷 run token → **銷毀沙盒**

### 7.3 沙盒的 LLM 呼叫（P4 的實作）

沙盒內 `ctx.llm()` 回傳一個 base URL 指向 `http://api:8000/internal/llm/v1` 的 LangChain chat model，
`api_key` 是 run token。真正的供應商金鑰只存在 `api` 服務的環境變數裡。
Internal Gateway 負責：換上真金鑰 → 呼叫供應商 → 計量 token → 寫 `llm_calls` → 回傳。

**沙盒內禁止**自建 LLM client、自行 `requests` 打外網（網段沒有對外路由，會直接失敗）。

## 8. 對外契約（Contract）

> **前後端與 SDK 都以此為準。** 完整定義見 [`specs/api-contract.md`](./api-contract.md)。

### 8.1 Agent 程式碼契約（沙盒內）
```python
from zentra_sdk import ZentraContext, agent
from langgraph.graph import StateGraph

@agent(name="my-agent", version="1.0.0")
def build(ctx: ZentraContext) -> StateGraph:
    """必須回傳未 compile 的 StateGraph；平台負責 compile 並注入事件 hook。"""
    ...
```
MVP 的 `ctx` 提供：`ctx.llm()`、`ctx.logger`、`ctx.emit()`、`ctx.config`。
**不提供** `ctx.tools`、`ctx.knowledge`、`ctx.secrets` —— 它們在 §11 的 MVP 外。

### 8.2 REST（節錄）
| Method | Path | 說明 |
|---|---|---|
| `POST` | `/v1/agents/{agent_id}/runs` | 建立 Run（非同步，202 + run_id） |
| `POST` | `/v1/agents/{agent_id}/runs:invoke` | 同步呼叫（60s 上限） |
| `GET` | `/v1/runs/{run_id}` | 查 Run 狀態 |
| `GET` | `/v1/runs/{run_id}/events` | SSE 事件串流 |
| `POST` | `/v1/runs/{run_id}/cancel` | 取消 |
| `POST` | `/v1/agents/{agent_id}/versions` | 上傳並發布新版本 |

### 8.3 MCP
每個發布的 Agent 自動掛載：`http://<host>/mcp/agents/{agent_id}`（Streamable HTTP）
- `tools/list` → 該 Agent 宣告的 entrypoints
- `tools/call` → 觸發一次 Run（預設同步，`_async: true` 回 `run_id`）

`resources/*` 在 MVP 不實作（它對應知識庫）。

### 8.4 錯誤格式（全平台統一）
```json
{ "error": { "code": "SANDBOX_TIMEOUT", "message": "...", "run_id": "...", "retryable": true } }
```

## 9. Run 狀態機（唯一定義）

```
QUEUED → SCHEDULING → RUNNING → ┬→ SUCCEEDED
                                ├→ FAILED
                                └→ CANCELLED

TIMEOUT / SANDBOX_ERROR / USER_CODE_ERROR 皆歸入 FAILED，以 error.code 區分
```

**前端、後端、QA 的狀態顯示與斷言都必須用這組字串，不得自創。**

> `WAITING_INPUT`（HITL）在 v0.2.0 **移出 MVP**，枚舉裡也移除了。
> 之後要加回來是向後相容的新增，但**在那之前不准有任何程式碼產生或斷言這個值**。

## 10. 非功能需求 (NFR)

| 項目 | 目標 | 變更說明 |
|---|---|---|
| 沙盒冷啟動 | P95 < 2s | 無 Warm Pool。共用 runtime image 已在本機，不需拉取 |
| API 額外延遲 | P95 < 100ms | 單機，從 50ms 放寬 |
| 併發 Run | 預設 **10**，由 Sandbox Manager semaphore 控；超過回 `QUOTA_EXCEEDED` | 單機容量決定 |
| Run 最長執行 | **15 分鐘硬上限**（wall-clock） | 取消 `q.agent.long` 與 6 小時 tier |
| 可用性 | **不設目標** | 單機即單點故障。`restart: unless-stopped` + 健康檢查，主機重開 = 停機。**誠實不承諾，而不是承諾做不到的數字** |
| 資料隔離 | 單租戶。資料表留 `owner_id` 欄位但**不做 RLS** | 多租戶在 MVP 外 |
| 稽核 | 所有 LLM 呼叫 100% 留存於 `llm_calls` | 沙盒沒有其他出口，這就是全部的外網活動 |

## 11. 範圍界線

### MVP 內（唯一主線）

```
上傳 Python → 發布成不可變版本 → 沙盒執行 → 自動 REST + MCP → Run 觀測（SSE）
```

具體：上傳式 Agent、版本發布與凍結、Celery 派工、Docker 沙盒四層隔離、
Internal Gateway（LLM 代理 + 事件回收）、REST + MCP 雙門面、Run 觀測、Console 五個畫面（原生 HTML/CSS/JS）、`zentra` CLI。

### MVP 外（架構留位，不寫程式碼）

平台組裝式 Agent、知識庫 / Ingestion / Hybrid Search、Tool Registry、Skill Registry、
HITL resume、Agent Flow 視覺化編排、多租戶與 RLS、Alias 流量切換、排程觸發（Beat）、
計費出帳、CVE / License 掃描、Warm Pool、Kubernetes 部署。

> **「架構留位」的意思是：介面切乾淨，不是預先建一個沒人用的抽象。**
> `SandboxDriver`、`ObjectStore`、`entrypoints` 這三個介面是留位；
> 空的資料表、沒人呼叫的 Registry 服務不是——那叫浪費。

## 12. 變更流程（所有 agent 必守）

1. 任何人（含 AI agent）發現規格缺漏或衝突 → **不得自行決定**，先在 `coordination/BOARD.md` 開一則 `SPEC-QUESTION`
2. `@architect` 裁決 → 若屬重大技術取捨，寫成 `specs/adr/ADR-XXXX-*.md`
3. 更新本文件，bump Spec 版本，在 §13 記一筆
4. 受影響的 agent 收到 handoff 後才開始改程式碼

**禁止**：在程式碼註解或 PR 描述裡「順手」改變規格。

## 13. 變更紀錄

| 版本 | 日期 | 變更 | 由 |
|---|---|---|---|
| v0.1.0 | 2026-09-20 | 初版：確立 P1–P5 五條核心立場、分層、技術選型、對外契約 | @architect |
| v0.1.1 | 2026-09-20 | 新增 ADR-0004（SandboxDriver 抽象與開發/正式環境分層）；ZEN-000 貫穿骨架排為第一個任務 | @architect |
| v0.2.1 | 2026-09-20 | **前端降維**（[ADR-0006](./adr/ADR-0006-vanilla-frontend.md)）：前端從 Next.js + React + TypeScript + Tailwind 改為**原生 HTML + CSS + ES modules，零建置**；取消 `console` 服務，改由 `api` 以 `StaticFiles` + 五條 `FileResponse` 路由服務，**長駐服務 5 → 4**；移除 Console BFF 這一層，Console 同源直接消費 `/v1`；移除 Vitest 與 pnpm。**P1–P5 不變** | @architect |
| v0.2.0 | 2026-09-20 | **降維改版**（[ADR-0005](./adr/ADR-0005-mvp-single-host-scope.md)）：部署 K8s → 單機 docker compose；基礎設施 11 → 5 個服務（砍 RabbitMQ / OpenSearch / MinIO / ClickHouse / Vault / 三個 Proxy）；分層 L0–L6 → 四層；三個 Proxy 合併為 Internal Gateway；範圍收斂成一條主線（砍平台組裝式、知識庫、Tool Registry、HITL）；沙盒五層 → 四層並明示無 gVisor；狀態機移除 `WAITING_INPUT`；NFR 可用性改為不設目標。**P1–P5 不變** | @architect |
