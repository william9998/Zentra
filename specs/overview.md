# Zentra 總覽

> `specs/` 底下每一份都是**現行事實**，不是歷史。要改行為 → 先改對應那一份，再改程式碼。
> 歷史（ADR、交接單、舊版規格）在 `git log`，不用讀。

## 產品一句話

開發者把 LangGraph Agent build 成 OCI image、push 到 Registry、用 digest 註冊；
平台把它凍結成版本、在**隔離容器**裡執行，自動開出 **REST** 與 **MCP** 兩個門面。

交付條件（**這張表變了，整份規格要重讀**）：

| 條件 | 值 |
|---|---|
| 使用場景 | 內部工具，單一租戶 |
| 程式碼來源 | **只跑自己信得過的程式碼** —— 隔離設計建立在這個前提上 |
| 部署 | 一台主機 `docker compose`，5 個長駐服務，本機與正式同一套 |

要跑別人寫的 Agent、出現第二個租戶、需要可用性承諾 → 現在的隔離與常駐容器設計都撐不住，要重新設計（優先評估 k3s + gVisor）。

## 五條不可妥協的立場（違反就是 bug）

| # | 立場 | 怎麼成立 |
|---|---|---|
| P1 | **使用者程式碼永遠不在平台行程內執行** | worker 只呼叫 Docker API。連「import 一下檢查 `build()` 在不在」都在一次性容器裡做 |
| P2 | **Agent 框架統一 LangChain + LangGraph** | 使用者交未 compile 的 `StateGraph`，平台 compile 並注入事件 hook |
| P3 | **每個 Agent 天生有 REST + MCP 兩個門面** | 同一份 `entrypoints` 生成 REST 驗證、OpenAPI 與 MCP tool schema |
| P4 | **沙盒內拿不到任何長期憑證** | 沙盒網段 `internal: true`，唯一對端是 `app` 的 Internal Gateway；只拿一個 Run 一把、跑完即撤銷的 run token |
| P5 | **版本不可變** | 以完整 OCI digest 識別，不接受 tag-only。一筆版本紀錄的 digest 永不改寫；同版號重新註冊 = 新增一筆並取代舊的。Run 綁定建立當下那一筆 |

## 架構

```
L0 接入 + 控制   app (FastAPI)   REST /v1 · MCP /mcp · SSE · Console 靜態檔 · Internal Gateway /internal
L1 派工          worker (Celery, Redis broker)   q.run · q.build
L2 執行          常駐 Agent 容器（預設）／一次性沙盒（退路、版本驗證）—— 都由 worker 經 docker.sock 建立
L3 資料          PostgreSQL · Redis · 內部 OCI Registry · Docker daemon image cache
```

compose 的 5 個長駐服務：`postgres` · `redis` · `registry` · `app` · `worker`。要加第六個先問。

| 網段 | 誰在上面 |
|---|---|
| `zentra-net` | postgres、redis、registry、app、worker |
| `zentra-sandbox-net`（`internal: true`，無對外路由） | app、所有 Agent 容器 |

**分層硬規則**
1. 沙盒（L2）不得直連 L3：沒有 Postgres、Redis、Registry 連線，一律經 Internal Gateway。
2. `postgres` / `redis` / `registry` / `worker` **絕不**接 `zentra-sandbox-net`。`app` 是唯一的橋。
3. 平台不得**對外**代理、轉發或嵌入任何 Registry HTTP 端點（`/v2/*`）—— `app` 橫跨兩個網段，任何「幫你查 registry」的端點都會變成沙盒的跳板。
   平台內部會用到 registry API 的只有兩處：push 通知進來時 `app` 回頭確認 tag、刪版本時 worker 刪 manifest。
4. 出口代打從 `app` 發出，所以 SSRF 防護在 `app` 這一側（見 [gateway.md](gateway.md)）。
5. 只有 `worker` 掛 `docker.sock`（等同主機 root，常駐風險，誠實記著）。
6. Console 沒有自己的容器，由 `app` 服務靜態檔，與 API 同源（見 [console-ui.md](console-ui.md)）。

## 文件地圖

| 想知道 | 讀 |
|---|---|
| 對外 REST 的每個端點、欄位、錯誤碼 | [api.md](api.md) |
| Run 怎麼從 API 走到容器、狀態機、終態誰決定 | [dispatch.md](dispatch.md) |
| Agent / 版本註冊、驗證、取代、下游 API key、刪除 | [agent-registry.md](agent-registry.md) |
| 主機資源怎麼分、上限、溢出、閒置回收、逾時 | [dispatcher-resources.md](dispatcher-resources.md) |
| 容器怎麼建、四層隔離、生命週期 | [container-orchestration.md](container-orchestration.md) |
| 沙盒唯一的對端：token、LLM 代打、出口、寄信 | [gateway.md](gateway.md) |
| MCP 門面 | [mcp.md](mcp.md) |
| 每張表、每個欄位 | [postgresql.md](postgresql.md) |
| 每種 Redis key | [redis.md](redis.md) |
| Console 的設計規則與元件 | [console-ui.md](console-ui.md) |

## 名詞

| 詞 | 意思 |
|---|---|
| Agent | 一個可被呼叫的智能體（metadata），底下有多個版本 |
| 版本（Agent Version） | `agent_id + semver` → 一個 OCI image digest。執行的最小單位 |
| Run | 一次呼叫。有 `run_id`、狀態機、事件流 |
| Entrypoint | Agent 對外暴露的一個入口，含 input/output JSON Schema。REST 與 MCP 共用 |
| 常駐容器 | 一個版本的長駐容器，同時跑多筆 Run（預設路徑） |
| 一次性沙盒 | 一筆 Run 一個容器、跑完即刪。常駐容器起不來時的退路；版本驗證也用它 |
| Internal Gateway | `app` 上的 `/internal/*`，沙盒唯一的網路對端 |
| run token | 綁一個 `run_id` 的短命憑證，沙盒呼叫 Gateway 用 |

## 技術選型（變更要先問）

Python 3.12 · FastAPI + Pydantic v2 · 純 SQL（`app` 用 asyncpg、`worker` 用 psycopg 3，**無 ORM、無 Alembic**）·
Celery 5.4（broker 與 result 都用 Redis）· LangChain + LangGraph · Docker（無 gVisor）· FastMCP（Streamable HTTP）·
PostgreSQL 16 · Redis 7 · `registry:2` · 前端原生 HTML/CSS/ES modules（**零建置、無任何執行期函式庫**）· uv。
觀測只有結構化 JSON log + `run_events` / `llm_calls` / `egress_calls` 三張表，不引入 OTel / Prometheus。

## 範圍：刻意不做

見 [`coordination/BOARD.md`](../coordination/BOARD.md) §刻意不做。重點：知識庫、Tool Registry、HITL（`WAITING_INPUT`）、
多租戶、K8s / gVisor、image tar 上傳、遠端 push 到內部 Registry、平台排程器、`ctx.secrets` 取值（永久否決）。

## 程式碼註解裡的舊編號

程式碼註解還會出現舊規格的編號，對照如下（不用回頭找原檔，它們已刪除）：

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
