# Zentra 總覽

> 這個資料夾每份文件都是「**現在的真實狀況**」，不是歷史紀錄。
> 想改行為：**先改對應的那份文件，再改程式碼，同一個 commit。** 文件和程式碼對不上就是 bug。
> 歷史（舊決策、舊規格）去看 `git log`。

## 這是什麼

一個放 AI Agent 的平台。

1. 開發者用 LangGraph 寫好 Agent，打包成 Docker image，推到平台內建的 Registry。
2. 在平台上「註冊」這個 image，平台就把它凍結成一個**版本**。
3. 有人呼叫時，平台在**隔離的容器**裡執行它。
4. 平台自動幫每個 Agent 開出 REST API；也可以把 Agent 勾選進平台**唯一的 MCP 接口**，讓別的 AI 平台當工具用。

**一個完整的流程長這樣：**

```
開發者                          平台                               呼叫端
  │  docker push image             │                                   │
  ├──────────────────────────────▶ │ Registry                          │
  │  POST /v1/agents/{id}/versions │                                   │
  ├──────────────────────────────▶ │ 驗證 image → 版本變 PUBLISHED      │
  │                                │ ◀───────────  POST /…/runs        │
  │                                │ 在容器裡跑 → 回傳結果 ─────────────▶ │
```

## 適用範圍（這張表一變，所有文件都要重讀）

| 條件 | 現在的值 |
|---|---|
| 用途 | 內部工具，**單一租戶**（只有一個團隊在用） |
| 程式碼來源 | **只跑自己信得過的程式碼**。隔離設計是建立在這個前提上 |
| 部署 | 一台主機用 `docker compose`，5 個長駐服務，開發和正式用同一套 |

如果要跑**別人寫的** Agent、出現第二個租戶、或需要保證不中斷，現在的隔離方式不夠，要重新設計（先評估 k3s + gVisor）。

## 五條不能破的規則

違反任何一條就是 bug。

| # | 規則 | 白話 | 怎麼做到 |
|---|---|---|---|
| P1 | 使用者的程式碼**永遠不在平台自己的行程裡跑** | 平台只負責派工，Agent 的程式碼一律在容器裡執行 | worker 只呼叫 Docker API。連「import 一下看 `build()` 在不在」都要開一次性容器做 |
| P2 | Agent 一律用 **LangChain + LangGraph** | 全平台只有一種 Agent 寫法 | 使用者交出還沒 compile 的 `StateGraph`，平台 compile 並接上事件 hook |
| P3 | 每個 Agent 都有 REST；也能被勾進唯一的 MCP 接口 | 寫一次 Agent，兩種方式都能呼叫 | REST 驗證、OpenAPI、MCP tool 的格式，全部來自同一份 `entrypoints` |
| P4 | 容器裡**拿不到任何長期憑證** | 就算 Agent 程式碼有問題，也偷不走 API key | 容器的網路**沒有對外路由**，唯一能連的是 `app` 的 Internal Gateway；容器只拿一把「這一筆 Run 專用、跑完就作廢」的 run token |
| P5 | **版本不可變** | 一個版本號指向的 image 永遠不變 | 用完整 digest（`sha256:…`）識別，不接受只有 tag 的寫法。同版號重新註冊 = 新增一筆並取代舊的 |

## 架構

```
L0 接入與控制   app（FastAPI）    REST /v1 · MCP /mcp · SSE · Console 網頁 · Internal Gateway /internal
L1 派工         worker（Celery）  佇列 q.run（派工）· q.build（發布、容器、回收）
L2 執行         Agent 容器        常駐容器（預設）／一次性沙盒（退路、驗證版本用）
L3 資料         PostgreSQL · Redis · 內部 Registry · Docker image 快取
```

compose 的 5 個長駐服務：`postgres` · `redis` · `registry` · `app` · `worker`。**要加第六個，先問。**

兩個 Docker 網路：

| 網路 | 誰在上面 |
|---|---|
| `zentra-net` | postgres、redis、registry、app、worker |
| `zentra-sandbox-net`（`internal: true`，**沒有對外路由**） | app、所有 Agent 容器 |

**`app` 是兩個網路之間唯一的橋。** 這就是 P4 的實體。

### 分層硬規則

1. 容器（L2）**不能直連**資料層（L3）：沒有 Postgres、Redis、Registry 連線，一律經 Internal Gateway。
2. `postgres` / `redis` / `registry` / `worker` **絕對不接** `zentra-sandbox-net`。
3. 平台**不對外**代理、轉發、嵌入 Registry 的 HTTP 端點（`/v2/*`）。
   原因：`app` 同時在兩個網路上，任何「幫你查 registry」的端點都會變成容器的跳板。
   平台內部只有兩處會用 registry API：push 通知進來時 `app` 回頭確認 tag；刪版本時 worker 刪 manifest。
4. 代替容器對外發的請求，是從 `app` 送出的。平台**不檢查目的位址是不是內網**，能連到哪裡全看那個 Agent 的白名單（見 [gateway.md](gateway.md)）。
   所以**不要把 `postgres` / `redis` / `registry` 加進白名單**：加了就等於讓容器繞過第 1、3 條。
5. 只有 `worker` 掛 `docker.sock`。這等於主機 root 權限，是一直存在的風險，這裡誠實記著。
6. Console 沒有自己的容器，由 `app` 提供網頁，和 API 同源（見 [console-ui.md](console-ui.md)）。

## 文件地圖

| 想知道 | 讀 |
|---|---|
| 對外 REST 有哪些端點、欄位、錯誤碼 | [api.md](api.md) |
| 一筆 Run 怎麼從 API 走到容器、狀態有哪些、誰決定結束 | [dispatch.md](dispatch.md) |
| **怎麼寫一個 Agent**（開發者從這裡開始） | [agent-development.md](agent-development.md) |
| Agent 與版本怎麼註冊、驗證、取代、刪除；下游 API key | [agent-registry.md](agent-registry.md) |
| 主機資源怎麼分、同時最多跑幾個、滿了怎麼辦、閒置回收、逾時 | [dispatcher-resources.md](dispatcher-resources.md) |
| 容器怎麼建、怎麼隔離、生命週期 | [container-orchestration.md](container-orchestration.md) |
| 容器唯一的對端：token、代打 LLM、對外請求、寄信 | [gateway.md](gateway.md) |
| MCP 接口怎麼用 | [mcp.md](mcp.md) |
| 每張資料表、每個欄位 | [postgresql.md](postgresql.md) |
| Redis 裡放什麼 | [redis.md](redis.md) |
| Console 網頁的規則與元件 | [console-ui.md](console-ui.md) |
| 現在該做什麼、什麼刻意不做 | [`coordination/BOARD.md`](../coordination/BOARD.md) |

## 名詞

| 詞 | 意思 |
|---|---|
| Agent | 一個可以被呼叫的智能體。只是名字和說明，底下有很多版本 |
| 版本（Agent Version） | `agent_id + semver`（例如 `1.2.0`）→ 對應**一個** image digest。實際執行的最小單位 |
| Run | 一次呼叫。有 `run_id`、狀態、事件流 |
| Entrypoint | Agent 對外開放的一個入口，附輸入／輸出的 JSON Schema。REST 和 MCP 共用；在 MCP 裡就是一個 tool |
| MCP key | 發給外部 AI 平台的憑證，用來連 `/mcp`。每把 key 勾選一組 Agent 當作它看得到的 tools |
| 常駐容器 | 一個版本一個長期開著的容器，同時處理多筆 Run（預設做法） |
| 一次性沙盒 | 一筆 Run 一個容器，跑完就刪。常駐容器起不來時的退路；驗證版本也用它 |
| Internal Gateway | `app` 上的 `/internal/*`，容器唯一能連的對象 |
| run token | 只綁一筆 Run 的短命憑證，容器用它呼叫 Gateway |

## 技術選型（要換先問）

Python 3.12 · FastAPI + Pydantic v2 · 純 SQL（`app` 用 asyncpg、`worker` 用 psycopg 3，**沒有 ORM、沒有 Alembic**）· Celery 5.4（broker 和 result 都用 Redis）· LangChain + LangGraph · Docker（沒有 gVisor）· FastMCP（Streamable HTTP）· PostgreSQL 16 · Redis 7 · `registry:2` · 前端是原生 HTML / CSS / ES modules（**零建置、不引入任何執行期函式庫**）· uv。

觀測只有結構化 JSON log，加上 `run_events` / `llm_calls` / `egress_calls` 三張表。平台程式不引入 OTel / Prometheus（不加 `/metrics`、不埋點）。

主機、容器、PostgreSQL、Redis 的維運監控在 [`monitoring/`](../monitoring/README.md)：Prometheus + Grafana 跑在獨立的監控主機，被監控的主機只裝現成的 exporter。它不屬於五個長駐服務，平台不依賴它。

## 刻意不做的

完整清單看 [`coordination/BOARD.md`](../coordination/BOARD.md) §刻意不做。重點：知識庫、Tool Registry、人工介入（`WAITING_INPUT`）、多租戶、K8s / gVisor、上傳 image tar、從外面直接 push 到內部 Registry、平台排程器、`ctx.secrets` 取值（永久否決）。

## 程式碼註解裡的舊編號

程式碼註解裡還會看到舊規格的編號。對照如下（舊檔已刪，不用找）：

| 舊編號 | 現在看 |
|---|---|
| `SPEC §2`（P1–P5）、`§5` 分層、`§11` 範圍 | 本文件 |
| `SPEC §7.1`、ADR-0008 / 0009 / 0016（OCI 註冊、內部 Registry、同版號取代） | [agent-registry.md](agent-registry.md) |
| `SPEC §7.2`、`§9` 狀態機、ADR-0002 / 0007（派工、終態） | [dispatch.md](dispatch.md) |
| `SPEC §6.2`、`§10`、ADR-0001 / 0004 / 0005 / 0012（隔離、資源、常駐容器） | [container-orchestration.md](container-orchestration.md)、[dispatcher-resources.md](dispatcher-resources.md) |
| `SPEC §7.3`、`§7.4`、`api-contract §4`、ADR-0011 / 0014（LLM 憑證、設定／機密／出口） | [gateway.md](gateway.md) |
| `api-contract §0–§2`、ZEN-015（API key、用量）、ADR-0013（硬刪） | [api.md](api.md)、[agent-registry.md](agent-registry.md) |
| `api-contract §3` | [mcp.md](mcp.md) |
| `ui-contract §x`、ADR-0006 | [console-ui.md](console-ui.md) |
| ADR-0015、`database/PostgreSQL.md` | [postgresql.md](postgresql.md) |
| `database/Redis.md` | [redis.md](redis.md) |
| ADR-0003 / 0010（打包與框架鎖定） | 本文件 P2、[agent-registry.md](agent-registry.md) §image 契約 |
