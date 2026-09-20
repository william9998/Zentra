# Zentra Agent Platform — 主規格書 (Master Spec)

> **這份文件是唯一事實來源 (Single Source of Truth)。**
> 所有 agent（架構師 / UI / 前端 / 後端 / QA）在開工前必須先讀這份文件。
> 任何與本文件衝突的程式碼、設計稿、測試，一律以本文件為準；
> 若發現本文件有錯，**先改這份文件**（走 §12 變更流程），再改程式碼。

| 欄位 | 值 |
|---|---|
| Spec 版本 | `v0.1.0` |
| 狀態 | `DRAFT` |
| 最後更新 | 2026-09-20 |
| 擁有者 | `@architect` |

---

## 1. 產品一句話

**Zentra 是一個 Agent 平台**：讓使用者以「平台組裝」或「上傳自己的 Python 程式碼」兩種方式建立 AI Agent，
每個 Agent 都在**隔離沙盒**中執行，並自動對外開放 **REST API** 與 **MCP** 兩種接口供外部系統呼叫。

## 2. 核心設計立場（不可妥協的五條）

| # | 立場 | 理由 |
|---|---|---|
| P1 | **使用者程式碼永遠不在平台行程內執行** | Celery worker 只做「派工 + 監看」，實際 `exec` 一律發生在沙盒。這是整個平台的安全邊界。 |
| P2 | **Agent 框架統一 LangChain + LangGraph** | 不接受其他 agent 框架。所有 agent 最終都是一個 `CompiledStateGraph`，平台才能統一做 checkpoint / 串流 / 中斷續跑 / 觀測。 |
| P3 | **每個 Agent 天生就有 API + MCP 兩個門面** | 不是額外功能，是 Runtime 出廠預設。開發者只寫 graph，介面由平台自動生成。 |
| P4 | **沙盒內拿不到任何長期憑證** | LLM key、Tool 憑證、DB 密碼都不下放。沙盒對外一律走 Egress Proxy / Tool Proxy，由平台代簽、代呼叫、留稽核。 |
| P5 | **Agent 版本不可變 (Immutable)** | 一旦 publish，`agent@version` 的 manifest + code bundle + lockfile 就凍結。要改就發新版本。線上流量靠 alias 切換。 |

## 3. 名詞定義

完整詞彙表見 [`specs/glossary.md`](./glossary.md)。最關鍵的六個：

- **Agent**：一個可被呼叫的智能體定義。由 `manifest` + `code bundle`（或平台組裝設定）構成。
- **Agent Version**：Agent 的不可變快照，格式 `agent_id@semver`。執行的最小單位。
- **Run**：一次 Agent 執行實例，有 `run_id`、狀態機、事件流。
- **Tool**：可被 Agent 呼叫的外部能力（HTTP API / Python function / 外接 MCP server）。註冊在 Tool Registry。
- **Skill**：一組被封裝好的 prompt + tool + 子圖的複用單元（選配）。註冊在 Skill Registry。
- **Sandbox**：執行 Agent 程式碼的隔離環境（容器 / microVM），有生命週期與資源上限。

## 4. 使用者與使用情境

| 角色 | 情境 | 主要介面 |
|---|---|---|
| **Agent 建置者** | 在 Console 勾選 Tools / Skills / 知識庫，組出一個 Agent | Web Console |
| **Agent 開發者** | 寫 Python（LangGraph）、`zentra deploy` 上傳 | CLI + SDK |
| **外部系統（業務系統 A / AI 平台系統）** | 以 REST 或 MCP 呼叫已發布的 Agent | API Gateway / MCP Gateway |
| **維運者** | 看 Run 的 trace、成本、失敗率，調整配額 | Console 觀測頁 |
| **平台管理員** | 審核上傳的套件、管理租戶配額與權限 | Admin Console |

## 5. 系統分層（權威定義）

架構圖見 [`docs/architecture/detailed-architecture.md`](../docs/architecture/detailed-architecture.md)。

```
L0 接入層 (Access)      API GW / MCP GW / Console BFF / WebSocket
L1 控制平面 (Control)    Agent Service, Registry, Build, Version, Policy, Secret
L2 派工層 (Dispatch)     Celery Broker + Beat + Worker Pools + Dead Letter
L3 執行平面 (Execution)  Sandbox Manager → Sandbox(Agent Runtime: LangGraph)
L4 知識層 (Knowledge)    Ingestion Pipeline → Vector + Keyword → Hybrid Search
L5 資料層 (Data)         Postgres / Redis / Object Store / ClickHouse
L6 觀測層 (Observability) OpenTelemetry / Langfuse / Prometheus / Audit Log
```

**分層規則**：上層只能呼叫下一層或同層的公開介面。L3 沙盒**不得**直接連 L5 資料層，一律透過 L1 的 Proxy。

## 6. 技術選型（已定案，變更需開 ADR）

| 範疇 | 選型 | 版本 | 備註 |
|---|---|---|---|
| 後端語言 | Python | 3.12 | |
| Web 框架 | FastAPI + Pydantic v2 | 0.115+ | 全 async |
| ORM / Migration | SQLAlchemy 2.0 + Alembic | | |
| 派工 | Celery | 5.4 | Broker=RabbitMQ、Result=Redis |
| Agent 框架 | LangChain + LangGraph | langchain 0.3 / langgraph 0.2+ | **唯一允許** |
| 沙盒 | Kubernetes + gVisor (runsc) | | 本機開發用 Docker + seccomp |
| 主資料庫 | PostgreSQL + pgvector | 16 | |
| 快取 / 串流 | Redis | 7 | Redis Streams 作事件匯流 |
| 物件儲存 | S3 / MinIO | | code bundle、文件原檔、run artifact |
| 全文檢索 | OpenSearch (BM25) | 2.x | 與 pgvector 組 Hybrid Search |
| 前端 | Next.js (App Router) + React + TypeScript | Next 15 / React 19 | |
| UI | Tailwind CSS + shadcn/ui | | 設計 token 見 `specs/ui-contract.md` |
| 前端狀態 | TanStack Query（server state）+ Zustand（client state） | | |
| Flow 畫布 | React Flow | 12 | Agent Flow 編輯器 |
| 測試 | pytest / Vitest / Playwright | | |
| 觀測 | OpenTelemetry + Langfuse + Prometheus | | |
| 套件管理 | uv（Python）、pnpm（Node） | | |

## 7. 核心流程（權威版本，時序圖見架構文件）

### 7.1 建立 Agent（上傳程式碼）
1. CLI `zentra deploy` 打包 `./` → 上傳 bundle 到 Object Store
2. Build Worker：解析 `zentra.yaml` → `uv lock` → 掃 CVE / License → 建出 venv layer → 產生 immutable image digest
3. 靜態驗證：必須 export 符合 §8.1 契約的 `build()`；禁用 API 掃描（`os.system`、`socket` 直連等）
4. 通過 → 寫入 Agent Registry，狀態 `PUBLISHED`，配發 API route 與 MCP endpoint

### 7.2 呼叫 Agent（非同步 Run）
1. 外部系統 `POST /v1/agents/{id}/runs` → Gateway 驗證 token / 配額 → 寫 `runs` 表 → 回 `run_id` (202)
2. API 送 Celery task 進 `q.agent.<tier>` 佇列
3. Runner Worker 取件 → 呼叫 Sandbox Manager 申請沙盒 → **worker 本身不執行使用者程式碼**
4. 沙盒啟動 Agent Runtime，載入 LangGraph，逐步執行；每個 node 的事件推到 Redis Streams
5. 呼叫者以 `GET /v1/runs/{id}/events` (SSE) 串流，或輪詢 `GET /v1/runs/{id}`
6. 結束 → 寫結果 + 用量 + trace → 沙盒銷毀

### 7.3 知識庫
文件上傳 → `q.ingest` → 解析 → 切分 → Embedding → 同時寫 pgvector + OpenSearch → Agent 透過 `ctx.knowledge.search()` 走 Hybrid Search + Rerank。

## 8. 對外契約（Contract）

> **前後端與 SDK 都以此為準。** 完整定義見 [`specs/api-contract.md`](./api-contract.md)。

### 8.1 Agent 程式碼契約（沙盒內）
```python
from zentra_sdk import ZentraContext, agent
from langgraph.graph import StateGraph

@agent(name="my-agent", version="1.0.0")
def build(ctx: ZentraContext) -> StateGraph:
    """必須回傳未 compile 的 StateGraph；平台負責 compile 並掛上 checkpointer。"""
    ...
```
`ctx` 提供：`ctx.llm()`、`ctx.tools`、`ctx.knowledge`、`ctx.secrets`、`ctx.logger`、`ctx.emit()`。
**沙盒內禁止**自建 LLM client、自行 `requests` 打外網（會被 Egress Proxy 擋）。

### 8.2 REST（節錄）
| Method | Path | 說明 |
|---|---|---|
| `POST` | `/v1/agents/{agent_id}/runs` | 建立 Run（非同步，202 + run_id） |
| `POST` | `/v1/agents/{agent_id}/runs:invoke` | 同步呼叫（有 60s 上限） |
| `GET` | `/v1/runs/{run_id}` | 查 Run 狀態 |
| `GET` | `/v1/runs/{run_id}/events` | SSE 事件串流 |
| `POST` | `/v1/runs/{run_id}/resume` | HITL 中斷後續跑 |
| `POST` | `/v1/runs/{run_id}/cancel` | 取消 |

### 8.3 MCP
每個發布的 Agent 自動掛載：`https://<host>/mcp/agents/{agent_id}` （Streamable HTTP）
- `tools/list` → 該 Agent 宣告的 entrypoints
- `tools/call` → 觸發一次 Run（同步回傳或回 run_id）
- `resources/list` → 該 Agent 綁定的知識庫集合

### 8.4 錯誤格式（全平台統一）
```json
{ "error": { "code": "SANDBOX_TIMEOUT", "message": "...", "run_id": "...", "retryable": true } }
```

## 9. Run 狀態機（唯一定義）

```
QUEUED → SCHEDULING → RUNNING → ┬→ SUCCEEDED
                                ├→ FAILED
                                ├→ CANCELLED
                                └→ WAITING_INPUT → RUNNING   (HITL)
                     TIMEOUT / SANDBOX_ERROR 皆歸入 FAILED，以 error.code 區分
```
**前端、後端、QA 的狀態顯示與斷言都必須用這組字串，不得自創。**

## 10. 非功能需求 (NFR)

| 項目 | 目標 |
|---|---|
| 沙盒冷啟動 | P95 < 3s（有 warm pool 時 < 800ms） |
| API Gateway 額外延遲 | P95 < 50ms |
| 單租戶併發 Run | 預設 50，可調 |
| Run 最長執行 | 預設 15 分鐘，長任務走 `q.agent.long`（上限 6 小時） |
| 可用性 | 控制平面 99.9% |
| 資料隔離 | 所有查詢強制帶 `tenant_id`，DB 層 RLS 兜底 |
| 稽核 | 所有工具呼叫與外網請求 100% 留存 |

## 11. 範圍界線

**MVP 內**：上傳式 Agent、平台組裝式 Agent、Tool Registry、知識庫 + Hybrid Search、REST + MCP 對外、Celery 派工、沙盒執行、Console 基本頁面、Run 觀測。
**MVP 外（但架構要留位）**：Agent Flow 視覺化編排、Skill Registry、多模型自動路由、A/B 流量、市集 / 分享、計費出帳。

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
