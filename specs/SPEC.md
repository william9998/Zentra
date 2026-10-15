# Zentra Agent Platform — 主規格書 (Master Spec)

> **這份文件是唯一事實來源 (Single Source of Truth)。**
> 所有 agent（架構師 / UI / 前端 / 後端 / QA）在開工前必須先讀這份文件。
> 任何與本文件衝突的程式碼、設計稿、測試，一律以本文件為準；
> 若發現本文件有錯，**先改這份文件**（走 §12 變更流程），再改程式碼。

| 欄位 | 值 |
|---|---|
| Spec 版本 | `v0.6.0` |
| 狀態 | `DRAFT` |
| 最後更新 | 2026-10-14 |
| 擁有者 | `@architect` |

> **v0.6.0 依 [ADR-0015](./adr/ADR-0015-plain-sql-no-orm.md) 拿掉 SQLAlchemy 與 Alembic**（ZEN-020）。
> `app` 用 asyncpg、`worker` 用 psycopg 3 直寫 SQL；migration 改為編號 `.sql` + 自寫 runner，
> 從既有的 Alembic `0008` 接起。**這不是效能修正**（ORM 本身不是瓶頸，瓶頸是連線握多久）。
> 新增一條硬規則：**連線只在一個工作單元內借出，絕不跨 HTTP、DNS、SMTP、Redis、sleep**（ADR-0015 §2）。
> **對外契約、schema、P1–P5 全部不變。**

> **v0.5.0 依 [ADR-0014](./adr/ADR-0014-per-agent-config-secrets-and-egress.md) 新增
> per-agent 的設定、機密與對外能力**（ZEN-019）。三件事拆成三層，性質不同所以不合併：
> **設定**（可變、明文、隨每筆 Run 進 `ctx.config`）、**機密**（Fernet 加密、**永遠不進沙盒**，
> 沙盒只拿得到名字）、**能力**（白名單 HTTP 代打 + 寄信，由 Internal Gateway 執行，見 §7.4）。
> **P1／P2／P3／P5 不變，P4 的字面一條沒鬆**（沙盒網段仍 `internal: true`、
> 沙盒 env 仍是精確集合、機密仍不進沙盒）——
> **但 P4 的目的被削弱了**：通用出口 proxy 是一條真正的外洩通道，
> 白名單擋得住「送到哪」，擋不住「送了什麼」。這個取捨由使用者裁決，
> 定價與緩解措施寫在 ADR-0014 §6，**讀那一段再判斷要不要沿用**。

> **v0.4.1 依 [ADR-0009 修訂 R2](./adr/ADR-0009-internal-registry-image-upload.md) 撤回 tar 上傳，
> 改為使用者在平台主機上直接 `docker push`。**
> 平台**不接收 image 檔案**。使用者在跑 compose 的那台主機上
> `docker push localhost:6000/<repo>:<tag>`，再用**既有的** JSON 端點以 digest 註冊。
> 因此**版本進場只有一條路徑**（JSON digest 註冊），image 本體可以在外部或內部 Registry，
> 平台不分支、不記錄。內部 OCI Registry（第 5 個長駐服務）**保留**，
> 它的 client 從「平台 worker」變成「主機上的 Docker daemon 與使用者 CLI」。
> v0.4.0 加入而 v0.4.1 撤回的：multipart 上傳、`LOAD`/`PUSH` stage、`artifact.origin` /
> `image_origin`、digest 三欄的 nullable 化、`uploads` volume、2 GiB 上限、磁碟餘裕的 `QUOTA_EXCEEDED`。
> **這些從未實作，所以相對於實際跑著的系統，v0.4.1 是零變更。**
> **`version_id` 語意不變、P1–P5 一條沒鬆**，P5 的可重現性比 v0.4.0 更強
> （digest 三欄維持 `NOT NULL`，不需要條件式 CHECK）。
>
> **v0.4.0 依 ADR-0009 R1 新增第二條 image 進場路徑（上傳 `docker save` tar）。
> ⚠️ 已由 v0.4.1 撤回，請勿實作。** 它留下的有效部分只有一項：內部 OCI Registry，
> 長駐服務 **4 → 5**（修訂 ADR-0005 的服務清單）。
>
> **v0.3.0 依 [ADR-0008](./adr/ADR-0008-oci-image-agent-packaging.md) 改變 Agent 交付格式。**
> 開發者自行 build 並 push OCI image，平台以 immutable digest 註冊、驗證與執行；
> image 取代尚未實作的 bundle + per-version venv 路徑。（當時是 4 個長駐服務；v0.4.0 後為 5 個。）P1–P5 不變。
>
> **v0.2.x 是一次降維改版。** 依 [ADR-0005](./adr/ADR-0005-mvp-single-host-scope.md)：
> 部署從 Kubernetes 收斂成**單機 docker compose**，分層從 L0–L6 七層降到**四層**，範圍收斂成**一條主線**。
> 再依 [ADR-0006](./adr/ADR-0006-vanilla-frontend.md)：前端改用**原生 HTML + CSS + JS（零建置）**，
> `console` 服務取消，由 `app` 直接服務靜態檔 —— 基礎設施從 11 個服務降到 **4 個**。
> **五條核心立場 P1–P5 一條都沒鬆** —— 降的是實作維度，不是立場。

---

## 1. 產品一句話

**Zentra 是一個 Agent 平台**：開發者把自己的 LangGraph Agent build 成 OCI image，
**push 到 Registry（外部的，或平台自己那個內部的）後以 digest 註冊**，
平台以 immutable digest 凍結成版本、在**隔離沙盒**中執行，並自動對外開放 **REST API** 與 **MCP** 兩種接口。

### 1.1 交付條件（決定了下面所有取捨）

| 條件 | 值 |
|---|---|
| 使用場景 | 內部工具 |
| 租戶 | 單一租戶 |
| 程式碼來源 | **只跑自己信得過的程式碼** |
| 交付目標 | MVP demo |
| 時程 | 2 個月（8 週） |
| 部署 | **單機 docker compose**（**5 個服務**，v0.4.0 新增內部 Registry），本機與正式是同一套。**發布版本需要在這台主機上有 shell**（v0.4.1，見 §7.1） |

**這張表變了，整份規格就要重讀。** 尤其「信任的程式碼」這條——
一旦要跑不是自己寫的 Agent，回去讀 [ADR-0001](./adr/ADR-0001-sandbox-isolation.md) 與 [ADR-0005](./adr/ADR-0005-mvp-single-host-scope.md) 的「何時該重新檢討」。

## 2. 核心設計立場（不可妥協的五條）

| # | 立場 | 理由 | 在 MVP 怎麼成立 |
|---|---|---|---|
| P1 | **使用者程式碼永遠不在平台行程內執行** | 這是整個平台的安全邊界，也是這個產品的核心技術 | Celery worker 只呼叫 Docker API。連「檢查 `build()` 是否存在」都在一次性沙盒裡做——`import` 使用者模組就是執行使用者程式碼 |
| P2 | **Agent 框架統一 LangChain + LangGraph** | 收斂成一種，串流 / 觀測 / 之後的 checkpoint 才只要寫一次 | 使用者交出未 compile 的 `StateGraph`，平台負責 compile |
| P3 | **每個 Agent 天生就有 API + MCP 兩個門面** | 不是額外功能，是出廠預設 | 同一份 `entrypoints` 生成 OpenAPI schema 與 MCP tool schema，MCP 掛在同一個 FastAPI app |
| P4 | **沙盒內拿不到任何長期憑證** | 即使沙盒被攻陷，損失也要可控 | 沙盒網段 `internal: true`，唯一對端是 **Internal Gateway**；只拿綁定單一 `run_id`、TTL = run timeout 的 **run token** |
| P5 | **Agent 版本不可變 (Immutable)** | 發布過的東西不能在腳下被換掉 | 發布與 Run 都使用完整 OCI manifest digest；tag-only reference 不接受 |

## 3. 名詞定義

完整詞彙表見 [`specs/glossary.md`](./glossary.md)。最關鍵的六個：

- **Agent**：一個可被呼叫的智能體定義，由 `manifest` + OCI image 構成。
- **Agent Version**：Agent 的不可變快照，`agent_id@semver`，以完整 OCI image digest 識別。執行的最小單位。
- **Run**：一次 Agent 執行實例，有 `run_id`、狀態機（§9）、事件流。
- **Entrypoint**：Agent 對外暴露的一個可呼叫入口。**同一份定義同時生成 REST 與 MCP schema**（P3 的實作）。
- **Sandbox**：執行 Agent 程式碼的隔離容器，**一 Run 一盒，結束即銷毀，絕不重用**。
- **Internal Gateway**：`app` 服務上的一組 `/internal/*` 路由，沙盒**唯一**的網路對端。代持 LLM 憑證、收事件、發 input。

## 4. 使用者與使用情境

| 角色 | 情境 | 主要介面 |
|---|---|---|
| **Agent 開發者** | 寫 LangGraph、build OCI image、push 到 Registry（外部 Registry，或**在平台主機上** push 到 `localhost:6000`），然後以 digest 註冊 | Docker/OCI CLI + Zentra Console/API/SDK |
| **外部系統** | 以 REST 或 MCP 呼叫已發布的 Agent | `/v1/...` · `/mcp/agents/{id}` |
| **維運者（＝同一個人）** | 看 Run 的事件流、失敗原因、token 用量 | Console |

MVP 沒有「平台組裝者」與「平台管理員」這兩個角色——它們對應的功能在 §11 的 MVP 外。

## 5. 系統分層（權威定義）

架構圖見 [`docs/architecture/detailed-architecture.md`](../docs/architecture/detailed-architecture.md)。

```
L0 接入 + 控制   api (FastAPI)  REST · MCP · SSE · Console 靜態檔 · Internal Gateway（LLM 代打 · 出口代打 · 寄信）· Agent Registry
L1 派工          worker (Celery, Redis broker)  q.run · q.build
L2 執行          Sandbox Manager → Sandbox（Docker，一 Run 一盒）
L3 資料          PostgreSQL · Redis · **內部 OCI Registry** · Docker daemon image cache
                （image 本體的權威來源＝`image_ref` 指向的 Registry，外部或內部都可以）
```

> **沒有 Console BFF。** Console 是原生 HTML/CSS/JS 靜態檔，由 `app` 服務，
> 與 API **同源**，直接消費 `/v1` —— 它是 `api-contract.md` 的第一個真實消費者（[ADR-0006](./adr/ADR-0006-vanilla-frontend.md)）。

觀測**不是一層**，是橫切：結構化 JSON 日誌 + `run_events` 表 + `llm_calls` 表。

**分層硬規則**：
1. 上層只能呼叫下一層或同層的公開介面。
2. **L2 沙盒不得直連 L3 資料層**——沒有 Postgres 連線、沒有 Redis 連線、**沒有 Registry 連線**，一律經 Internal Gateway。
3. 沙盒的網路對端**有且只有一個**：`app`。
   **內部 Registry 絕對不接 `zentra-sandbox-net`**；沙盒的 image 是 daemon 給的，沙盒自己不拉 image。
4. **平台不得代理、轉發、嵌入或連結任何 Registry HTTP 端點**（`/v2/*` 含 `_catalog`）。
   Registry 的 HTTP API 是**維運介面**，不是產品介面：它不出現在 `app` 的任何路由、
   Console 的任何 `fetch`、OpenAPI 或 MCP tool schema 裡（[ADR-0009 R2-5](./adr/ADR-0009-internal-registry-image-upload.md)）。
   理由很具體：`app` 同時掛在 `zentra-net` 與 `zentra-sandbox-net` 上，
   `app` 上任何「幫你查 registry」的端點都會變成沙盒觸達 Registry 的跳板，直接打穿 P4。
   **Registry 對沙盒不可達，要靠「沒有任何一條路可以到」保證，不是靠沙盒不知道位址。**
5. **出口代打是從 `app` 發出去的，所以 SSRF 防護在 `app` 這一側**（v0.5.0，§7.4）。
   `app` 同時掛在 `zentra-net` 與 `zentra-sandbox-net` 上，一個「幫你打這個網址」的端點
   如果不檢查解析結果，就等於把 `app` 變成沙盒觸達 `postgres` / `redis` / `registry` 的跳板。
   因此出口 proxy **必須**擋掉解析到 private / loopback / link-local / metadata 位址的目標，
   而且**不跟隨 redirect**（跟隨等於讓對方決定下一跳）。這是硬規則 4 的同一個理由，
   換到另一條路徑上。

## 6. 技術選型（已定案，變更需開 ADR）

| 範疇 | 選型 | 版本 | 備註 |
|---|---|---|---|
| 後端語言 | Python | 3.12 | |
| Web 框架 | FastAPI + Pydantic v2 | 0.115+ | 全 async |
| 資料存取 / Migration | **純 SQL**：`app` 用 asyncpg、`worker` 用 psycopg 3；migration 是編號 `.sql` + 自寫 runner | asyncpg 0.30+ / psycopg 3.2+ | **無 ORM、無 SQLAlchemy Core、無 Alembic**。連線只在一個工作單元內借出，不跨任何外部 I/O；migration 的 `.sql` 是 schema 唯一事實來源（[ADR-0015](./adr/ADR-0015-plain-sql-no-orm.md)） |
| 派工 | Celery | 5.4 | **Broker 與 Result 都用 Redis**（不用 RabbitMQ） |
| Agent 框架 | LangChain + LangGraph | langchain 0.3 / langgraph 0.2+ | **唯一允許** |
| 沙盒 | **Docker**（`DockerSandboxDriver`） | | 四層隔離，**無 gVisor**，見 §6.2 |
| MCP | FastMCP（Streamable HTTP） | | 掛載在同一個 FastAPI app |
| 資料庫 | PostgreSQL | 16 | 用 `pgvector/pgvector:pg16` 映像——擴充先備著，MVP 不用 |
| 快取 / 串流 | Redis | 7 | Celery broker + result + Streams 事件匯流 |
| Agent artifact | **OCI image digest** | OCI Image Spec | image 本體在外部或內部 Registry；PostgreSQL 只存 metadata；VM 保留 Docker image cache |
| 內部 Registry | CNCF distribution `registry:2` | 2.8 | 只掛 `zentra-net`、只發布 `127.0.0.1:6000`、**無 TLS / 無認證 / 無自動 GC**。client 只有**主機上的 Docker daemon 與使用者 CLI**，沒有任何平台服務會連它（[ADR-0009](./adr/ADR-0009-internal-registry-image-upload.md)） |
| Image runtime contract | `zentra.image/v1` | | 固定 executable + validate/run mode；平台不執行 `pip install` |
| 前端 | **原生 HTML + CSS + ES modules** | — | **零建置**：無 bundler、無 TypeScript、無 npm / pnpm（[ADR-0006](./adr/ADR-0006-vanilla-frontend.md)） |
| UI | 原生 CSS custom properties | | 設計 token 見 [`specs/ui-contract.md`](./ui-contract.md)，**本來就是純 CSS 變數** |
| 前端狀態 | 一個 `store.js`（發布／訂閱，約 40 行） | | 不引入任何執行期函式庫 |
| 串流 | 原生 `EventSource` | | SSE 是瀏覽器原生能力 |
| 測試 | pytest / Playwright | | **無 Vitest**；Playwright 測瀏覽器，跟框架無關 |
| 觀測 | 結構化 JSON log + DB 事件表 | | 不引入 OTel / Langfuse / Prometheus |
| 套件管理 | uv（Python）**only** | | 前端沒有套件管理員，因為沒有套件 |

### 6.1 執行環境（5 個長駐服務）

```
docker compose（長駐，5 個）              Sandbox Manager 動態建立（不在 compose 檔裡）
  postgres                                  sandbox-<run_id>
  redis                                     每 Run 一個，結束即銷毀
  registry ← L3（內部 OCI Registry）
  api      ← L0（含 Console 靜態檔）
  worker   ← L1
```

**內部 Registry（v0.4.0 新增，v0.4.1 改變用途，ADR-0009）**：
只掛 `zentra-net`，**只發布 `127.0.0.1:6000:5000`，不得綁 `0.0.0.0`**。
`REGISTRY_STORAGE_DELETE_ENABLED=false`，儲存在 named volume `registrydata:/var/lib/registry`。

- **它的 client 是誰**：主機上的 Docker daemon（pull）與**使用者的 `docker push`**。
  **沒有任何平台服務會連它**——`app` 與 `worker` 都不連，因此也**不需要** `depends_on: registry`。
- **為什麼一定要發布 loopback port**：拓撲，不是偏好。push/pull 的執行者是主機上的 daemon，
  它不在 `zentra-net` 上、解析不到服務名。`localhost` 同時讓 daemon 預設允許明文 HTTP，
  不必改 `daemon.json` 的 `insecure-registries`。
- **loopback 綁定同時就是存取控制**：只有該主機上的行程連得到，這就是「只限同機 push」的執行手段。
  **綁 `0.0.0.0` 需新 ADR，且該 ADR 必須先解決 TLS 與認證**（[ADR-0009 R2-1](./adr/ADR-0009-internal-registry-image-upload.md)）。
- **前提：Docker daemon 與 compose 同一台主機。** 遠端 `DOCKER_HOST` 會 push 成功、pull 失敗。

**備份範圍（誠實標示）**：`registrydata` volume 與 `pgdata` **同等重要**。
`image_ref` 指向 `localhost:6000` 的 `PUBLISHED` 版本，其 image 本體只存在 `registrydata` 裡；
刪掉它＝那些版本永久不可執行。MVP **不提供自動備份、不做自動 GC**。

**容量與回收的責任歸屬（v0.4.1）**：使用者直接 push，平台**不在那條路徑上**，
因此容量與清理是**維運者的責任**，平台不做配額、不做預檢、不對 Registry 內容負責。
- 未被任何 `agent_versions` 引用的 repository：**平台不刪、不列、不管**（無法分辨垃圾與「五分鐘後要註冊的東西」）。
- 磁碟滿掉的表現：使用者的 `docker push` 在 client 端失敗，**平台不會顯示任何訊號**。已 `PUBLISHED` 的版本不受影響。
- 回收是維運程序：`GET /v2/_catalog` 對照 `agent_versions` 中 `PUBLISHED` 的 `image_digest`，
  停機 → 暫時開啟 delete → `registry garbage-collect` → 關回去。
- ⚠️ **`registry garbage-collect --delete-untagged` 絕對不准用**：使用者用同一個 tag 推第二次會讓
  前一個 manifest 變成 untagged，而它可能正是某個 `PUBLISHED` 版本的 `image_digest`。
  這也是推薦「tag 用 semver」的原因。

**Console 沒有自己的容器**：`app` 用 `StaticFiles` 掛載 `apps/console/`，
並以五條 `FileResponse` 路由給出乾淨網址（`/console/agents`、`/console/runs/{run_id}` …），
`/` 轉址到 `/console/agents`。**沒有前端路由器** —— 深層連結與上一頁由伺服器處理。

**沙盒為什麼不寫在 compose 裡**：compose 宣告的是「開機就跑、一直在那」的長駐服務；
沙盒是「使用者打一次 API 才開一個、跑完就刪」的動態資源。這是程式邏輯，YAML 表達不了。

### 6.2 沙盒隔離（四層，核心技術）

| 層 | 措施 | 驗收方式 |
|---|---|---|
| **L1 網路** | 沙盒網段 `internal: true`（無對外路由）；唯一對端是 `app` 的 Internal Gateway | 沙盒內 curl `1.1.1.1`、`169.254.169.254`、`postgres:5432` **必須全部失敗** |
| **L2 核心** | `cap_drop ALL`、`no-new-privileges`、non-root `65534:65534`、Docker 預設 seccomp。**無 gVisor** | `mount -t proc`、`unshare`、`ptrace` **必須失敗**；`id` 必須非 root |
| **L3 資源** | `mem_limit` 2g、`pids_limit` 256、`nano_cpus` 2 core、無 swap、wall-clock timeout、stdout 上限 10MB | fork bomb、記憶體炸彈、無窮迴圈**必須被殺掉且不影響鄰居** |
| **L4 檔案** | `read_only=True` rootfs；`/app/code`、`/app/venv` 唯讀 bind；`/workspace` tmpfs 512m；**無 hostPath、無 docker.sock** | 寫 `/`、`/app` **必須失敗**；`/workspace` 可寫且隨盒銷毀 |
| **L5 應用** | SDK 攔截層：禁自建 LLM client、禁裸 socket | 沙盒內 `import socket` 直連外網**必須被擋** |

**缺的是 L2 的核心隔離（gVisor）。** `SandboxDriver.capabilities()` 必須回報 `l2_kernel_isolation: false`，
安全測試對應項標 **`skipped`，絕不標 `passed`**。

**沒有全部驗證過，不准說沙盒做好了。** 每一項右欄都要有對應的自動化測試。

## 7. 核心流程（權威版本，時序圖見架構文件）

### 7.1 發布一個 Agent 版本

**版本進場只有一條路徑**（v0.4.1，[ADR-0009 R2](./adr/ADR-0009-internal-registry-image-upload.md)）：
`POST /v1/agents/{id}/versions` + `application/json` + canonical digest reference。
**平台不接收 image 檔案。** image 本體可以在兩種地方，平台**不分支、不記錄、沒有對應欄位**：

| image 放哪裡 | 開發者怎麼放進去 | `image_ref` 長相 |
|---|---|---|
| 外部 Registry | 自己 push 到 GHCR / 自建 registry（ADR-0008） | `ghcr.io/acme/agent@sha256:…` |
| **平台內部 Registry** | **在跑平台的那台主機上** `docker push localhost:6000/<repo>:<tag>` | `localhost:6000/<repo>@sha256:…` |

內部 Registry 的受支援用法（**只限同機**，遠端 push 需新 ADR 且必須先解決 TLS 與認證）：

```bash
docker tag my-agent:dev localhost:6000/my-agent:1.0.0   # 推薦 tag 用 semver（見 §6.1 的 GC 陷阱）
docker push localhost:6000/my-agent:1.0.0                # 最後一行印出 digest: sha256:<64 hex>
docker inspect --format='{{index .RepoDigests 0}}' localhost:6000/my-agent:1.0.0
```

1. 進場：提供 `repository@sha256:<digest>`（tag-only reference 不接受）。
   `repository` 可以含 registry host 與 port，`localhost:6000/my-agent` 合法
2. `POST /v1/agents/{id}/versions` 送出 `semver` + `image_ref`
3. `app` 寫 `agent_versions`（`BUILDING`），送 Celery task 進 `q.build`；
   **PostgreSQL 不保存 image blob**
4. worker 令 daemon pull exact digest（外部 Registry 用平台側 credential；
   `localhost:6000` 不帶 credential，因為內部 Registry 沒有認證），
   接著 inspect platform/label/runtime contract，並以 `RepoDigests` 確認 digest 對得上
5. 起一次性 validation container：以 uid 65534、唯讀 rootfs、internal 網段執行
   `/opt/zentra/bin/zentra-agent validate`；import 使用者模組、驗證 `build()` 與未 compile `StateGraph`，
   抽出 entrypoints schema，再以短效 validation token POST 回 Internal Gateway
6. 通過 → 保存完整 digest、manifest、entrypoints 並轉 `PUBLISHED`，配發 REST/MCP/OpenAPI（P3）；
   失敗 → `FAILED + BUILD_FAILED`（`details.stage` ∈ `PULL|INSPECT|VALIDATE`）。
   不論結果都撤銷 token、銷毀 validation container

**P5 的不變式**：沒有可重新 pull 的 canonical digest reference，**不得**成為 `PUBLISHED`。
`image_ref` / `image_digest` / `version_id` 三欄**任何狀態下都是 `NOT NULL`**，
再加上 `INSPECT` 階段的 `RepoDigests` 比對，共同保證這件事。
（v0.4.0 曾為了上傳路徑把三欄 nullable 化並改用條件式 CHECK，v0.4.1 撤回——`NOT NULL` 嚴格更強。）

**`image_ref` 的保存規則（P5，實作必守）**：`agent_versions.image_ref`
**原封不動保存呼叫端註冊的字串**。registry 前綴正規化（`docker.io/library/x` ≡ `docker.io/x` ≡ `x`）
只可以發生在**比較時**，不得改寫已保存的值。
判斷哪一段是 registry host 的規則：repository 第一段含 `.` 或 `:`，或等於 `localhost` → 它就是
registry host，**必須原樣保留**。因此 `localhost:6000/alpine` **不等於** `alpine`；
任何把 `localhost:6000/x` 正規化成 `x` 或 `docker.io/x` 的實作都是嚴重 bug
（[ADR-0009 R2-6](./adr/ADR-0009-internal-registry-image-upload.md)）。

### 7.2 呼叫 Agent（非同步 Run）

1. `POST /v1/agents/{id}/runs` → `app` 驗證 input schema → 寫 `runs`（`QUEUED`）→ 回 `202 { run_id }`
2. 送 Celery task 進 `q.run`
3. worker 取件（`acks_late=True` + 冪等）→ 向 Sandbox Manager 申請沙盒
   → **worker 本身不執行使用者程式碼（P1）**
4. Sandbox Manager 從 `agent_versions.image_ref` 取出已發布的完整 digest，建立容器；
   平台覆寫 command 為 `/opt/zentra/bin/zentra-agent run`，注入 `ZENTRA_RUN_TOKEN`、接上沙盒網段
5. 沙盒內 Agent Runtime：bootstrap → `import build()` → 平台 compile（注入事件 hook）→ 逐節點執行
6. 每個事件帶重送時不變的 `event_id`，`POST /internal/runs/{run_id}/events` → `app` 以
   `(run_id,event_id)` 冪等寫 `run_events` 表，再 `XADD` Redis Stream；SSE 以 DB `seq` 去重
7. 呼叫者 `GET /v1/runs/{id}/events`（SSE，支援 `Last-Event-ID` 續傳）或輪詢 `GET /v1/runs/{id}`
8. 沙盒先經 `POST /internal/runs/{run_id}/result` 回寫 output 或 error；worker 確認沙盒結束後才決定終態，
   寫入 `runs` 並發出唯一的終局 `run.status` 事件 → 撤銷 run token → **銷毀沙盒**。
   沙盒事件或結果請求本身不得宣告 Run 已成功；沙盒崩潰而未回寫結果時，worker 寫 `FAILED` 與錯誤碼。

### 7.3 沙盒的 LLM 呼叫（P4 的實作）

沙盒內 `ctx.llm()` 回傳一個 base URL 指向 `http://app:8000/internal/llm/v1` 的 LangChain chat model，
`api_key` 是 run token。真正的供應商金鑰只存在 `app` 服務的環境變數裡。
Internal Gateway 負責：換上真金鑰 → 呼叫供應商 → 計量 token → 寫 `llm_calls` → 回傳。

**金鑰的選擇順序**（[ADR-0011](./adr/ADR-0011-per-agent-llm-credentials.md)）：
該 Agent 自己註冊的憑證 → 平台預設。Agent 的憑證**綁 Agent 不綁版本**：
發新版不用重填，更新則所有版本立刻一起變。憑證以 Fernet 加密後存 `agent_llm_credentials`，
平台未設定 `ZENTRA_SECRET_KEY` 時**拒絕儲存**而非退回明文。
沒有任何端點讀得出金鑰；`llm_calls.credential_source` 記錄每次用的是誰的金鑰。

**沙盒內禁止**自建 LLM client、自行 `requests` 打外網（網段沒有對外路由，會直接失敗）。

### 7.4 沙盒的設定、機密與對外呼叫（v0.5.0，[ADR-0014](./adr/ADR-0014-per-agent-config-secrets-and-egress.md)）

三件事性質不同，所以是三層，**不合併**（完整契約見 [`api-contract.md`](./api-contract.md) §2.6–§2.10、§4.1–§4.4）：

| 層 | 存哪裡 | 到得了沙盒嗎 |
|---|---|---|
| **設定** | `agent_settings`，明文 JSON，`agent_id` 為鍵 | **會** —— 每筆 Run 隨 bootstrap／工作項送進 `ctx.config` |
| **機密** | `agent_secrets`，**Fernet 加密**（要解得回來才能登入 SMTP），`(agent_id, name)` 為鍵 | **永遠不會**。沙盒只拿得到 `secret_names` 名字清單 |
| **能力** | `agent_egress_rules`（主機白名單）、`agent_email_sender` | 規則不進沙盒；沙盒只看得到請求成不成功 |

三層都**綁 Agent 不綁版本**，與 LLM 憑證同一個結構與同一組理由（[ADR-0011](./adr/ADR-0011-per-agent-llm-credentials.md)）：
發新版自動繼承，更新則所有版本立刻一起變。刪 Agent 時三層全部硬刪（[ADR-0013](./adr/ADR-0013-hard-delete-agents-and-images.md)）。

**設定不走環境變數**（這是實作最容易走偏的地方）：

1. 沙盒 env 的精確集合檢查（`_validate_environment()`）**不放寬**，
   這次**不新增任何環境變數**。那條檢查是 P4 的第一道關。
2. 常駐容器一個容器服務多筆 Run（[ADR-0012](./adr/ADR-0012-resident-agent-container.md)）：
   設定寫進 env 就做不到「改完下一筆生效」，併發時還會串到別人身上。
3. 所以設定掛在 `ctx.config`，**每筆 Run 重新帶，兩條派工路徑都要帶** ——
   bootstrap（一次性沙盒）與工作項（常駐容器）。**只改一條等於沒改。**
4. `config` 是頂層欄位，**不得合併進 `manifest`**：manifest 是版本的不可變內容。

**對外呼叫一律經 Internal Gateway 代打，沙盒仍然沒有對外路由**：

- `POST /internal/egress/request`：白名單內的 `https` GET／POST。
  預設拒絕、無萬用字元、不跟隨 redirect、擋內網位址、**沙盒不得自帶認證 header**
  （要帶認證由規則指名一個 secret，平台注入）、有大小與次數上限。
- `POST /internal/notify/email`：SMTP 寄信。密碼是該 Agent 的一個 secret，
  收件人必須在 `allowed_recipients` 白名單內。
- **兩者都要 run token，每一次都寫一列 `egress_calls`**（含被擋下的）。
  只靠 `run_id` 就要還原得出「這次 Run 往外送了什麼」。

> ⚠️ **通用出口 proxy 是一條真正的外洩通道。** 白名單擋得住「送到哪」，
> 擋不住「送了什麼」—— `GET` 的 query string 就是資料。GET-only 與大小上限
> 只是壓低頻寬，不是消除通道。這個取捨成立的前提是交付條件 §1.1 的
> **「只跑自己信得過的程式碼」**。那條變了就回去讀 ADR-0014 §6 與「何時該重新檢討」。

## 8. 對外契約（Contract）

> **前後端與 SDK 都以此為準。** 完整定義見 [`specs/api-contract.md`](./api-contract.md)。

### 8.1 Agent image 與程式碼契約（沙盒內）

Image 必須標記 `io.zentra.image-contract=zentra.image/v1`，並提供
`/opt/zentra/bin/zentra-agent validate|run`。平台以 non-root + read-only rootfs 啟動，
只提供 `/workspace` tmpfs；容器內不開對外 HTTP port。REST/MCP 是平台 `app` 的門面。

```python
from zentra_sdk import ZentraContext, agent
from langgraph.graph import StateGraph

@agent(name="my-agent", version="1.0.0")
def build(ctx: ZentraContext) -> StateGraph:
    """必須回傳未 compile 的 StateGraph；平台負責 compile 並注入事件 hook。"""
    ...
```
MVP 的 `ctx` 提供：`ctx.llm()`、`ctx.logger`、`ctx.emit()`、`ctx.config`，
以及 v0.5.0 新增的 `ctx.secret_names`、`ctx.http`、`ctx.send_email()`。
**不提供** `ctx.tools`、`ctx.knowledge`、`ctx.secrets`（取值）—— 前兩個在 §11 的 MVP 外，
第三個是刻意**永遠不做**（機密不進沙盒，見 §7.4）。

| ctx 能力 | 是什麼 | 要注意的 |
|---|---|---|
| `ctx.config` | 這個 Agent 的可變設定，dict（api-contract §2.6） | **v0.5.0 之前它永遠是 `{}`** —— 有 property 但沒有人往裡面放東西。現在由 bootstrap／工作項帶進來。**不要在 `build()` 裡對某個鍵做硬性要求**：validation 通過之後設定還會變，少一個鍵會讓每一筆 Run 都在 build 階段死掉，而版本狀態仍是 `PUBLISHED`。用 `.get()` 帶預設值，或在節點裡讀。**validation 沙盒一律用空的 `config` 驗證**（api-contract §4.1 第 4 點、[ADR-0014 修訂 R1](./adr/ADR-0014-per-agent-config-secrets-and-egress.md)），所以違反這條規則的 agent 會在**發布時**就失敗，而不是等使用者哪天清掉設定 |
| `ctx.secret_names` | 這個 Agent 設了哪些機密的**名字**，唯讀集合 | 只有名字，**沒有值**。用途是 fail fast：第一個節點就說得出「缺 `SMTP_PASSWORD`」，而不是跑完整段分析才在最後一步失敗 |
| `ctx.http` | 經 Gateway 代打的對外 HTTP（api-contract §4.2） | 回的是**信封**：上游狀態碼在 `status` 裡，不是例外。所以「主源失敗換備援」寫得出來。不要自己 `import requests`，網段不通 |
| `ctx.send_email()` | 經 Gateway 寄信（api-contract §4.4） | 寄件者、帳號、密碼、收件人白名單都在平台側。回 `202` 表示 SMTP 收下了，不表示對方讀到了。**不冪等**，重送就是再寄一封 |

**簽章是契約的一部分（v0.4.0 裁決 Q-008）**：`build` 必須接受**且只接受一個位置參數**，
參數名稱不限，回傳未 compile 的 `StateGraph`。`validate` mode 必須在呼叫前檢查 arity，
不符時回 `BUILD_FAILED` 並在 message 明說「`build()` 必須接受一個 ctx 參數」——
這個錯誤只在驗證沙盒裡看得到，訊息是使用者唯一的線索。

**SDK 依賴由 SDK 自己宣告（v0.4.0 裁決 Q-009）**：`packages/sdk/pyproject.toml` 宣告
`jsonschema` / `httpx` / `langgraph` 等 runtime dependency。image 契約只要求
「image 內有一份可用的 `zentra_sdk`」，**不列舉它的傳遞依賴**（列舉會過期）。
runtime 啟動先做 import 自檢，缺件時報出缺哪個模組。

**官方 base image（v0.4.0 裁決 Q-010）**：`zentra/agent-base:1.0`（`docker/agent-base.Dockerfile`）
提供 Python 3.12 + `zentra_sdk` + LangGraph + `/opt/zentra/bin/zentra-agent` + label + `USER 65534:65534`。
使用者的 Dockerfile 縮成 `FROM zentra/agent-base:1.0` + 一行 `COPY`。
**本機 build，不發佈公開 Registry、不簽章、不多架構**，tag 固定 `1.0`。

### 8.2 REST（節錄）
| Method | Path | 說明 |
|---|---|---|
| `POST` | `/v1/agents/{agent_id}/runs` | 建立 Run（非同步，202 + run_id） |
| `POST` | `/v1/agents/{agent_id}/runs:invoke` | 同步呼叫（60s 上限） |
| `GET` | `/v1/runs/{run_id}` | 查 Run 狀態 |
| `GET` | `/v1/runs/{run_id}/events` | SSE 事件串流 |
| `POST` | `/v1/runs/{run_id}/cancel` | 取消 |
| `POST` | `/v1/agents/{agent_id}/versions` | 發布新版本：**只接 JSON**，以 canonical OCI digest 註冊。平台不接收 image 檔案 |

Run 的 `trigger` 表示**平台接入協定**，不是 UI 身分：所有 `/v1/.../runs` HTTP 呼叫（包含同源 Console）
都由伺服器記為 `API`；只有 FastMCP 的 `tools/call` 記為 `MCP`。同源、`Referer`、`Origin`、
自訂 header 或 request body 都不能作為可信的 Console 來源證明，因此狀態值沒有 `CONSOLE`。

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
| 沙盒冷啟動 | P95 < 2s（已發布 image 在本機 cache 時） | 無 Warm Pool；Registry pull 屬發布階段，不計入 Run 冷啟 |
| API 額外延遲 | P95 < 100ms | 單機，從 50ms 放寬 |
| 併發 Run | 預設 **10**，由 Sandbox Manager semaphore 控；超過回 `QUOTA_EXCEEDED` | 單機容量決定 |
| Run 最長執行 | **15 分鐘硬上限**（wall-clock） | 取消 `q.agent.long` 與 6 小時 tier |
| 可用性 | **不設目標** | 單機即單點故障。`restart: unless-stopped` + 健康檢查，主機重開 = 停機。**誠實不承諾，而不是承諾做不到的數字** |
| 資料隔離 | 單租戶。資料表留 `owner_id` 欄位但**不做 RLS** | 多租戶在 MVP 外 |
| 稽核 | 所有 LLM 呼叫 100% 留存於 `llm_calls`；**所有對外 HTTP 與寄信 100% 留存於 `egress_calls`，含被擋下的**（v0.5.0） | v0.4.x 時「沙盒沒有其他出口」，`llm_calls` 就是全部的外網活動。**v0.5.0 之後不再是** —— 多了 §7.4 的兩條出口，所以多一張表，兩張合起來才是全部。**被擋下的也要記**：少了它，「為什麼我的 agent 連不出去」在畫面上沒有任何線索 |
| 出口上限（v0.5.0） | request ≤ 64 KiB、response ≤ 1 MiB、單次逾時 ≤ 15s、每筆 Run ≤ 100 次；寄信每 Run ≤ 5 封、每 Agent 每小時 ≤ 60 封 | **超量不截斷，直接當錯誤**。次數上限是為了不被交易所封 IP（被封的是整台主機）與不被 Gmail 鎖帳號。全部可由環境變數調整，預設值寫在 api-contract §4.2／§4.4 |
| `egress_calls` 保留期 | **不設**，跟 `llm_calls` 一樣只增不刪 | 一支每分鐘抓一次行情的 agent 一天就是上千列。**這是已知會長大的東西**，保留期與彙總記在 BOARD 待辦，不在 ZEN-019 範圍 |
| image 進場 | **平台不接收 image 檔案**，沒有上傳端點、沒有大小上限、沒有續傳問題 | v0.4.1 撤回 v0.4.0 的 tar 上傳；image 經 `docker push` 進 Registry，layer 去重與重試由 Docker 負責 |
| 內部 Registry 容量 | 上限＝主機磁碟。**無自動 GC、無配額系統，責任在維運者** | 平台不在 push 路徑上，**不做預檢也沒有訊號**：磁碟滿了是使用者的 `docker push` 在 client 端失敗，Console 什麼都不會顯示。已 `PUBLISHED` 的版本不受影響（§6.1） |

## 11. 範圍界線

### MVP 內（唯一主線）

```
push OCI image（外部 Registry 或主機上的 localhost:6000）→ 以 digest 註冊
  → 發布不可變版本 → 沙盒執行 → 自動 REST + MCP → Run 觀測（SSE）
```

具體：OCI image 型 Agent、**單一進場路徑（JSON digest 註冊）＋ 內部 Registry 讓使用者同機 push**、
版本發布與 digest 凍結、Celery 派工、Docker 沙盒四層隔離、
Internal Gateway（LLM 代理 + 事件回收 + **對外 HTTP 代打 + 寄信**，v0.5.0）、
**per-agent 設定／機密／出口白名單**（v0.5.0，§7.4）、
REST + MCP 雙門面、Run 觀測、Console 五個畫面（原生 HTML/CSS/JS）、`zentra` CLI。

### MVP 外（架構留位，不寫程式碼）

平台組裝式 Agent、知識庫 / Ingestion / Hybrid Search、Tool Registry、Skill Registry、
HITL resume、Agent Flow 視覺化編排、多租戶與 RLS、Alias 流量切換、排程觸發（Beat）、
計費出帳、CVE / License / image 簽章掃描、Warm Pool、Kubernetes 部署、
Python bundle/venv 發布、**Dockerfile 上傳與遠端 build service**、
內部 Registry 的 TLS / 認證 / 自動 GC / 配額 / 自動備份 / 遠端（非同機）push、
版本或 image 的刪除功能、**平台接收 image 檔案（`docker save` tar 上傳）**、
斷點續傳與分塊上傳、Registry 瀏覽 UI 或任何代理 Registry HTTP 端點的平台功能。

v0.5.0 追加在 MVP 外的（[ADR-0014](./adr/ADR-0014-per-agent-config-secrets-and-egress.md) 明確否決或延後）：
**沙盒可取值的 `ctx.secrets`**（永久否決，不是延後）、
**把設定注入沙盒環境變數**（永久否決）、
出口內容檢查／DLP、設定與機密的版本史、
交易所專用端點與行情快取、
出口白名單的萬用字元主機／跟隨 redirect／非 `https`／`PUT`・`DELETE`／沙盒自帶 header
（這四項各自都要回頭改 ADR-0014，不得在 code review 裡順手放行）、
`egress_calls` 的保留期與彙總。

> v0.4.0 把「image tar 直傳」與「平台內建 OCI Registry」移出本清單；
> **v0.4.1 把「image tar 直傳」放回 MVP 外**（[ADR-0009 R2](./adr/ADR-0009-internal-registry-image-upload.md) 撤回它），
> **只有「平台內建 OCI Registry」維持在 MVP 內**。要把 tar 上傳加回來需新 ADR ——
> 唯一站得住腳的理由是「出現只有瀏覽器、沒有主機 shell 的發布者」。

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
| v0.6.0 | 2026-10-14 | 依 [ADR-0015](./adr/ADR-0015-plain-sql-no-orm.md) 修改 §6「ORM / Migration」一列為「資料存取 / Migration：純 SQL」（ZEN-020）：移除 SQLAlchemy（ORM 與 Core）與 Alembic；`app` 用 asyncpg、`worker` 用 psycopg 3；migration 改為編號 `.sql` + 自寫 runner（只認 `schema_migrations` 版本表，只有 up）；既有資料庫由一次性腳本 `scripts/adopt-legacy-schema.sh` 從 `alembic_version='0008'` 接起（`0009` 時刪除）；新增硬規則「連線只在一個工作單元內借出，不跨 HTTP／DNS／SMTP／Redis／sleep」，把 2026-10-14 的連線熱修（`fbea4d5`）變成結構，並以 `app`／`worker` 連線的 `idle_in_transaction_session_timeout = 60s` 為安全網（觸發時沿用 `INTERNAL_ERROR`）；`DATABASE_URL` 接受舊的 `+driver` 前綴；`DB_POOL_SIZE`／`DB_MAX_OVERFLOW` 保留並對應到 asyncpg 的 `min_size`／`max_size`。**明記這不是效能修正**。**api-contract 不變、schema 不變、P1–P5 不變** | @architect |
| v0.5.0 | 2026-09-30 | 依 [ADR-0014](./adr/ADR-0014-per-agent-config-secrets-and-egress.md) 新增 **per-agent 設定／機密／對外能力三層**（ZEN-019）：新增 §7.4；§5 新增分層硬規則 5（出口代打的 SSRF 防護在 `app` 這一側）；§8.1 的 ctx 能力表補上 `ctx.config` **從空殼變成真的會帶值**，新增 `ctx.secret_names` / `ctx.http` / `ctx.send_email()`，並把 `ctx.secrets`（取值）標為**永久不做**；§10 新增 `egress_calls` 稽核、出口上限與保留期三列，並修正「沙盒沒有其他出口」這個已不再成立的前提；§11 兩張清單各自更新。契約對應 api-contract v0.12.0。**P1／P2／P3／P5 不變，P4 的字面一條沒鬆**（沙盒網段仍 `internal: true`、env 仍是精確集合、機密仍不進沙盒）；**但 P4 的目的被削弱**，定價見 ADR-0014 §6 | @architect |
| v0.4.1 | 2026-09-23 | 依 [ADR-0009 修訂 R2](./adr/ADR-0009-internal-registry-image-upload.md) **撤回 v0.4.0 的 tar 上傳路徑**，改為使用者在平台主機上直接 `docker push localhost:6000/<repo>:<tag>` 後以既有 JSON 端點註冊（**只限同機**，不綁 `0.0.0.0`，不加 TLS/認證）。移除 multipart、`LOAD`/`PUSH` stage、`artifact.origin`/`image_origin`、digest 三欄 nullable 化、`uploads` volume、2 GiB 上限、磁碟餘裕的 `QUOTA_EXCEEDED`；digest 三欄恢復 `NOT NULL`。新增：§5 硬規則 4（平台不得代理 Registry HTTP 端點）、§6.1 容量與 GC 責任歸屬與 `--delete-untagged` 禁令、§7.1 `image_ref` 正規化護欄。內部 OCI Registry（5 個長駐服務）**保留**。v0.4.0 的新增從未實作，**相對於實際跑著的系統是零變更**。P1–P5 與 `version_id` 語意不變 | @architect |
| v0.4.0 | 2026-09-22 | 依 [ADR-0009](./adr/ADR-0009-internal-registry-image-upload.md) 新增 image tar 上傳路徑與**內部 OCI Registry**（長駐服務 4 → 5，修訂 ADR-0005 的服務清單）；明定兩條路徑的匯流點與「`origin` 不得影響執行期行為」；把 P5 可重現性寫成不變式（`RepoDigests` 非空 + DB CHECK）；裁決 Q-008（`build(ctx)` 簽章）、Q-009（SDK 自行宣告依賴）、Q-010（官方 base image）。`version_id` 語意、Run 狀態機、錯誤 code、沙盒隔離與 P1–P5 均未變 | @architect |
| v0.3.1 | 2026-09-22 | 裁決 Run `trigger` 為可信的接入協定：REST 與 Console 共用 `/v1`，一律為 `API`；FastMCP handler 為 `MCP`；移除無法可靠判定的 `CONSOLE`。不新增 client 可控 discriminator | @architect |
| v0.3.0 | 2026-09-22 | 依 [ADR-0008](./adr/ADR-0008-oci-image-agent-packaging.md) 將尚未實作的 Python bundle + per-version venv 發布路徑改為 OCI image digest 註冊；image 本體在既有 Registry，PostgreSQL 只存 metadata；新增 `zentra.image/v1` validate/run 契約。REST/MCP → Celery → 一 Run 一盒 → 銷毀與 P1–P5 不變 | @architect |
| v0.2.2 | 2026-09-21 | 澄清 Run 完成權責：沙盒經 Internal Gateway 回寫結果，worker 確認退出並唯一決定終態、終局 SSE 事件、token 撤銷與沙盒銷毀；事件以 `(run_id,event_id)` 冪等落庫、SSE 以 DB `seq` 去重；不改 P1–P5 或 MVP 範圍 | @architect |
| v0.1.0 | 2026-09-20 | 初版：確立 P1–P5 五條核心立場、分層、技術選型、對外契約 | @architect |
| v0.1.1 | 2026-09-20 | 新增 ADR-0004（SandboxDriver 抽象與開發/正式環境分層）；ZEN-000 貫穿骨架排為第一個任務 | @architect |
| v0.2.1 | 2026-09-20 | **前端降維**（[ADR-0006](./adr/ADR-0006-vanilla-frontend.md)）：前端從 Next.js + React + TypeScript + Tailwind 改為**原生 HTML + CSS + ES modules，零建置**；取消 `console` 服務，改由 `app` 以 `StaticFiles` + 五條 `FileResponse` 路由服務，**長駐服務 5 → 4**；移除 Console BFF 這一層，Console 同源直接消費 `/v1`；移除 Vitest 與 pnpm。**P1–P5 不變** | @architect |
| v0.2.0 | 2026-09-20 | **降維改版**（[ADR-0005](./adr/ADR-0005-mvp-single-host-scope.md)）：部署 K8s → 單機 docker compose；基礎設施 11 → 5 個服務（砍 RabbitMQ / OpenSearch / MinIO / ClickHouse / Vault / 三個 Proxy）；分層 L0–L6 → 四層；三個 Proxy 合併為 Internal Gateway；範圍收斂成一條主線（砍平台組裝式、知識庫、Tool Registry、HITL）；沙盒五層 → 四層並明示無 gVisor；狀態機移除 `WAITING_INPUT`；NFR 可用性改為不設目標。**P1–P5 不變** | @architect |
