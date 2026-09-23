# Zentra Agent Platform — 詳細架構圖

> 規格定義以 [`specs/SPEC.md`](../../specs/SPEC.md) 為準，本文件只負責「畫清楚」。
>
> **本文件對應 SPEC `v0.3.0`。** OCI image 註冊與執行的完整流程見
> [`oci-image-agent-flow.md`](oci-image-agent-flow.md) 與 [ADR-0008](../../specs/adr/ADR-0008-oci-image-agent-packaging.md)。
> 單機四服務與安全邊界仍依 [ADR-0005](../../specs/adr/ADR-0005-mvp-single-host-scope.md)
> 與 [ADR-0006](../../specs/adr/ADR-0006-vanilla-frontend.md)：
> 單機 docker compose、四層分層、一條主線，Console 是 `api` 服務的原生 HTML/CSS/JS 靜態檔。
> v0.1 的 K8s 拓撲、三個 Proxy、知識層、Agent Flow、Next.js console 服務已移除，對照表見 §10。
>
> ⚠️ **SPEC `v0.4.0`（[ADR-0009](../../specs/adr/ADR-0009-internal-registry-image-upload.md)）新增了第 5 個長駐服務
> `registry`（內部 OCI Registry）。**
> 本文中所有「4 個長駐服務」的敘述請讀成 **5 個**（postgres · redis · **registry** · api · worker）。
> `registry` 只掛 `zentra-net`、只發布 `127.0.0.1:5000`，**絕不接 `zentra-sandbox-net`**，
> 沙盒的網路對端仍然只有 `api` 一個。本文其餘圖示尚未重畫。
>
> ⚠️ **SPEC `v0.4.1`（ADR-0009 修訂 R2）改變了 `registry` 的用途**：
> 「上傳 image tar」**已撤回**，改為使用者**在平台主機上**直接
> `docker push localhost:5000/<repo>:<tag>`，再用既有的 JSON 端點以 digest 註冊。
> 因此 `api` 與 `worker` **完全不在 push 路徑上**，也不連 `registry`
> （registry 的 client 只有主機 daemon 與使用者 CLI）。
> 另新增一條硬規則：**平台不得代理、轉發、嵌入或連結任何 Registry HTTP 端點**——
> `api` 同時掛在兩個網段上，那會變成沙盒觸達 Registry 的跳板（P4）。
> 資料流見 [`oci-image-agent-flow.md`](oci-image-agent-flow.md) §內部 Registry。

## 0. 總覽

![Zentra 系統架構](zentra-architecture.svg)

### 一次請求如何啟動沙盒

[![Zentra 請求到沙盒的整體資料流](zentra-request-flow.svg)](zentra-request-flow.svg)

這張圖由左到右呈現 REST／MCP 呼叫如何建立 Run、經 Redis 與 Celery 派工、透過 Docker API
動態建立一 Run 一盒的 Sandbox，以及事件／結果如何經 Internal Gateway 回寫 PostgreSQL 並串流到前端。
安全標示以目前實作為準：有 Docker hardening，**沒有 gVisor**，`l2_kernel_isolation=false`。

> 圖中的 `data/bundles · data/venvs` 是 v0.2 的舊 artifact 標示；v0.3 的權威資料流已改為
> 「既有 OCI Registry → VM Docker image cache」，PostgreSQL 只存 digest 與 metadata。

一台主機、一個 `docker compose`、**四個**長駐服務，加上**每個 Run 動態建立、結束即銷毀**的沙盒容器。
沙盒掛在一個沒有對外路由的網段上，唯一的對端是 `api` 服務的 Internal Gateway。
Console 沒有自己的容器 —— 它是 `api` 服務出去的一包靜態檔。

---

## 1. 系統全景（單機拓撲）

```mermaid
graph TB
    subgraph EXT["對外呼叫端"]
        direction LR
        E1["業務系統 / Script"]
        E2["MCP Client<br/>Claude · IDE"]
        E3["zentra CLI"]
        E4["瀏覽器"]
    end

    subgraph HOST["單一主機 — docker compose"]
        subgraph L0["L0 接入 + 控制 · api (FastAPI)"]
            direction LR
            A1["REST /v1/*"]
            A2["MCP /mcp/agents/{id}"]
            A3["SSE /v1/runs/{id}/events"]
            A4["Console 靜態檔<br/>/console/* · StaticFiles"]
            A5["Agent Registry<br/>Interface Generator"]
            A6["🔐 Internal Gateway<br/>/internal/*"]
        end

        subgraph L1["L1 派工 · worker (Celery)"]
            direction LR
            Q1{{"q.run"}}
            Q2{{"q.build"}}
            W1["runner-worker<br/>⚠️ 不執行使用者程式碼"]
            W2["build-worker"]
        end

        subgraph L2["L2 執行"]
            direction TB
            SM["Sandbox Manager<br/>生命週期 · 併發 semaphore"]
            SBX["Sandbox — 一 Run 一盒<br/>LangGraph CompiledStateGraph"]
        end

        subgraph L3["L3 資料"]
            direction LR
            DB[("postgres<br/>agents · versions<br/>runs · run_events · llm_calls")]
            RD[("redis<br/>Celery broker/result<br/>Streams 事件匯流")]
            FS[("本機 volume<br/>data/bundles · data/venvs")]
        end
    end

    LLM["🌍 LLM 供應商"]

    E1 --> A1
    E2 --> A2
    E3 --> A1
    E4 -->|"HTML/CSS/JS"| A4
    E4 -->|"同源 fetch /v1"| A1
    E1 --> A3

    A1 & A2 --> A5
    A1 & A2 -->|"建立 Run"| Q1
    A1 -->|"上傳版本"| Q2
    Q1 --> W1
    Q2 --> W2
    W1 --> SM
    W2 -->|"裝 venv · 起驗證沙盒"| SM
    SM -->|"create / destroy"| SBX

    SBX ===|"唯一出口"| A6
    A6 -->|"代持金鑰"| LLM
    A6 -->|"寫事件"| DB
    A6 -->|"XADD"| RD
    RD -->|"XREAD"| A3

    A5 --> DB
    W1 --> DB
    W2 --> FS
    SM -->|"唯讀掛載"| FS

    classDef ext fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
    classDef acc fill:#e8eaf6,stroke:#283593,stroke-width:2px
    classDef disp fill:#fff8e1,stroke:#ef6c00,stroke-width:2px
    classDef exec fill:#fce4ec,stroke:#c2185b,stroke-width:2px
    classDef data fill:#f3e5f5,stroke:#6a1b9a,stroke-width:2px
    class E1,E2,E3,E4 ext
    class A1,A2,A3,A4,A5 acc
    class Q1,Q2,W1,W2 disp
    class SM,SBX exec
    class DB,RD,FS data
    style A6 fill:#fff3e0,stroke:#e65100,stroke-width:3px
    style SBX fill:#fce4ec,stroke:#c2185b,stroke-width:3px
```

**三條硬規則**（SPEC §5）：
1. 沙盒**不得**直連 Postgres 或 Redis。
2. 沙盒的網路對端**有且只有一個**：`api`。
3. `worker` 全程不 `import` 使用者模組（P1）。

**Console 沒有 BFF**：瀏覽器先跟 `api` 拿 HTML/CSS/JS，再用同源 `fetch` 打 `/v1` ——
就是外部系統用的那份 API（[ADR-0006](../../specs/adr/ADR-0006-vanilla-frontend.md)）。

---

## 2. 發布一個版本（P1 + P5）

> v0.3 的權威發布圖見 [`oci-image-agent-flow.md`](oci-image-agent-flow.md)。下圖是 v0.2 bundle 設計的歷史圖，
> 已由 ADR-0008 取代，不得作為實作契約。

```mermaid
sequenceDiagram
    autonumber
    participant CLI as zentra CLI
    participant API as api
    participant MQ as q.build
    participant BW as build-worker
    participant SB as 驗證沙盒<br/>(--mode=validate)
    participant FS as data/ volume
    participant PG as postgres

    CLI->>API: POST /v1/agents/{id}/versions<br/>(multipart: bundle.tar.gz + semver)
    API->>FS: 落檔
    API->>PG: INSERT agent_versions (BUILDING)
    API-->>CLI: 202 { version_id, status: BUILDING }
    API->>MQ: send_task

    MQ->>BW: 取件
    BW->>FS: 解壓 → sha256 → version_id
    Note over BW,SB: setup.py 等同任意程式碼<br/>所以依賴安裝也在容器內
    BW->>SB: uv pip install --target data/venvs/{version_id}
    BW->>SB: 起驗證沙盒（唯讀掛 code + venv）
    SB->>SB: import 使用者模組<br/>檢查 export build() → StateGraph
    SB->>SB: 抽出 entrypoints 的 input/output schema
    SB->>API: POST /internal/versions/{id}/validation
    API->>PG: 寫 manifest + entrypoints

    alt 驗證通過
        BW->>FS: chmod -R a-w（bundle + venv 凍結，P5）
        BW->>PG: UPDATE PUBLISHED
        Note over API: 配發 REST 路由 + MCP 端點（P3）
    else 驗證失敗
        BW->>PG: UPDATE FAILED + error
        BW->>FS: 清掉 venv
    end
```

**P1 的細節**：連「檢查 `build()` 是否存在」都在沙盒裡做。
因為 `import` 使用者模組就是執行使用者程式碼——build-worker 的行程裡不能有這一步。

**P5 的實作**：`version_id` 是 bundle 的 SHA-256，發布後目錄設唯讀。
同一個 `semver` 重複上傳回 `409`。

---

## 3. 一次 Run 的完整時序（核心流程）

```mermaid
sequenceDiagram
    autonumber
    participant C as 外部系統
    participant API as api
    participant RD as redis
    participant W as runner-worker
    participant SM as Sandbox Manager
    participant SB as Sandbox<br/>(LangGraph Runtime)
    participant GW as Internal Gateway<br/>(api 的 /internal/*)
    participant PG as postgres

    C->>API: POST /v1/agents/{id}/runs
    API->>API: 驗 api_key · 驗 input schema
    API->>PG: INSERT runs (QUEUED)
    API->>RD: send_task(q.run, run_id)
    API-->>C: 202 { run_id }

    C->>API: GET /v1/runs/{id}/events (SSE)
    API->>RD: XREAD run:{id}

    RD->>W: 取件 (acks_late, prefetch=1)
    W->>PG: UPDATE SCHEDULING
    W->>SM: acquire(agent@version, limits)
    Note over SM: semaphore 上限 10<br/>滿了 → QUOTA_EXCEEDED
    SM->>SB: 建容器（唯讀 code + venv · tmpfs · internal 網段）
    SM->>SB: 注入 ZENTRA_RUN_TOKEN（TTL = timeout）
    W->>PG: UPDATE RUNNING

    SB->>GW: GET /internal/runs/{id}/bootstrap
    GW-->>SB: manifest + entrypoint + input
    SB->>SB: import build() → 平台 compile（注入事件 hook）

    loop 每個 LangGraph node
        SB->>SB: 執行 node
        SB->>GW: POST /internal/llm/v1/chat/completions
        GW->>GW: 換真金鑰 · 計量 token · 寫 llm_calls
        GW-->>SB: 結果
        SB->>GW: POST /internal/runs/{id}/events
        GW->>PG: INSERT run_events
        GW->>RD: XADD run:{id}
        RD-->>API: 串流
        API-->>C: SSE event
    end

    SB->>GW: POST /internal/runs/{id}/result（只存 output/error/usage）
    W->>PG: 確認沙盒退出與結果 → UPDATE 終態
    W->>PG: INSERT 唯一終局 run.status 事件
    W->>RD: XADD 終局事件
    W->>RD: 撤銷 run token
    W->>SM: release
    SM->>SB: destroy（不重用）
    API-->>C: SSE run.status SUCCEEDED
```

**安全重點（P1）**：`runner-worker` 全程只做「申請沙盒、回寫狀態」，
**從來沒有在自己的行程裡 import 或 exec 使用者的程式碼**。

**P4 的重點**：沙盒拿到的只有一個綁死 `run_id`、TTL = timeout 的 token，
而且它的網段根本沒有對外路由——即使 token 外洩，拿著它也只能對 Internal Gateway 說話。

---

## 4. 沙盒隔離模型（核心技術）

```mermaid
graph TB
    subgraph HOST["單一主機（沙盒與平台服務同機 ⚠️）"]
        subgraph SBX["Sandbox 容器 — 一 Run 一盒"]
            subgraph L5["L5 應用層"]
                A1["Zentra SDK 攔截層<br/>禁自建 LLM client<br/>禁裸 socket"]
            end
            subgraph L4["L4 檔案系統"]
                F1["read_only rootfs"]
                F2["/app/code · /app/venv 唯讀 bind"]
                F3["/workspace tmpfs 512m<br/>隨盒銷毀"]
                F4["無 hostPath · 無 docker.sock"]
            end
            subgraph L3["L3 資源 (cgroups v2)"]
                RC["mem 2g · pids 256<br/>cpu 2 core · 無 swap<br/>wall-clock ≤ 15min<br/>stdout ≤ 10MB"]
            end
            subgraph L2["L2 核心"]
                K1["cap_drop ALL<br/>no-new-privileges<br/>non-root 65534"]
                K2["Docker 預設 seccomp"]
                KX["❌ 無 gVisor"]
            end
            subgraph L1["L1 網路"]
                N1["zentra-sandbox-net<br/>internal: true<br/>（無對外路由）"]
            end
        end

        GW["🔐 Internal Gateway<br/>api 的 /internal/*<br/>代持金鑰 · 計量 · 稽核"]
        PLAT["postgres · redis · worker"]
    end

    NET["🌍 LLM 供應商"]

    SBX ===|"唯一對端"| GW
    SBX -.->|"❌ 不通"| PLAT
    SBX -.->|"❌ 不通"| NET
    GW --> NET
    GW --> PLAT

    style SBX fill:#fce4ec,stroke:#c2185b,stroke-width:3px
    style GW fill:#fff3e0,stroke:#e65100,stroke-width:3px
    style KX fill:#ffebee,stroke:#c62828,stroke-width:2px
```

### 誠實標示：這個等級擋得住什麼、擋不住什麼

| 擋得住 | 擋不住 |
|---|---|
| 讀寫主機檔案系統 | **核心層的容器逃逸**（無 gVisor） |
| 直連資料庫、Redis、內網 | 沙盒與平台服務**同機**，逃逸後鄰居就是憑證 |
| 直接打外網、打雲端 metadata 端點 | |
| 偷長期憑證（沙盒裡根本沒有） | |
| 吃光 CPU / 記憶體 / process 影響鄰居 | |

**因此：這個架構適用於信任的程式碼，不適用於公開多租戶。**
`SandboxDriver.capabilities()` 必須回報 `l2_kernel_isolation: false`，
對應的安全測試標 `skipped` 而非 `passed`。要跑不可信程式碼 → 回頭讀 [ADR-0001](../../specs/adr/ADR-0001-sandbox-isolation.md)。

### 沙盒生命週期

```mermaid
stateDiagram-v2
    [*] --> Creating: runner-worker 申請<br/>(semaphore 有名額)
    [*] --> Rejected: semaphore 滿<br/>→ QUOTA_EXCEEDED
    Creating --> Initializing: 容器起來<br/>注入 run token
    Initializing --> Running: bootstrap → build() → compile
    Running --> Draining: 完成 / 失敗 / 逾時 / 取消
    Draining --> Destroyed: 撤銷 token · 回收 tmpfs
    Destroyed --> [*]
    Rejected --> [*]

    note right of Destroyed
        沙盒永不重用
        destroy() 必須冪等
    end note
```

---

## 5. Celery 派工

```mermaid
graph LR
    subgraph PROD["任務生產者"]
        S1["api — 建立 Run"]
        S2["api — 上傳版本"]
    end

    subgraph BROKER["Redis — 佇列"]
        direction TB
        Q1["q.run<br/>一次 Run 一個 task"]
        Q2["q.build<br/>解壓 · venv · 驗證"]
        DLQ["☠️ dead letter<br/>重試耗盡"]
    end

    subgraph WORKERS["worker 容器"]
        direction TB
        WA["runner-worker<br/>--concurrency=10<br/>acks_late=True · prefetch=1"]
        WC["build-worker<br/>--concurrency=2"]
    end

    S1 --> Q1 --> WA
    S2 --> Q2 --> WC
    WA -.重試耗盡.-> DLQ
    WA --> SM["Sandbox Manager"]
    WC --> SM
    RES[("Redis<br/>result backend")]
    WA & WC --> RES

    style DLQ fill:#ffebee,stroke:#c62828,stroke-width:2px
    style SM fill:#fce4ec,stroke:#c2185b,stroke-width:2px
```

**從 v0.1 降維的部分**：broker 從 RabbitMQ 換成 Redis；六條佇列降成兩條
（砍 `q.agent.long` / `q.agent.priority` / `q.ingest` / `q.eval`）；
無 Beat、無 KEDA 自動擴縮——單機的 worker 併發用 `--concurrency` 寫死。

### Celery 與 LangGraph 的職責邊界（常見混淆點）

```mermaid
graph TB
    subgraph CEL["Celery 負責 — 跨行程"]
        C1["佇列"]
        C2["重試 · 逾時 · 死信"]
        C3["worker 併發控制"]
    end
    subgraph LG["LangGraph 負責 — 單一 Agent 行程內"]
        L1["節點狀態機與條件分支"]
        L2["串流 token / 事件"]
        L3["子圖與工具迴圈"]
    end
    RULE["📏 判準：<br/>需要跨行程 / 需要排隊 / 需要重試 → Celery<br/>只是同一 Agent 內部的下一步 → LangGraph node"]
    CEL --> RULE
    LG --> RULE
    style RULE fill:#fff9c4,stroke:#f9a825,stroke-width:3px
```

判準不變，見 [ADR-0002](../../specs/adr/ADR-0002-celery-langgraph-boundary.md)。
**一次 Run 就是一個 Celery task，不管圖裡有幾個節點。**

---

## 6. Agent Runtime 內部（沙盒中）

```mermaid
graph TB
    subgraph RT["Agent Runtime（沙盒內唯一入口）"]
        BOOT["Bootstrap<br/>GET /internal/runs/{id}/bootstrap<br/>→ import build() → 平台 compile"]
        subgraph GRAPH["CompiledStateGraph"]
            direction TB
            N0(["START"])
            N1["使用者自己定義的節點"]
            N2["llm_node → ctx.llm()"]
            N3["finalize"]
            N9(["END"])
            N0 --> N1 --> N2 --> N3 --> N9
        end
        HOOK["平台注入的事件 hook<br/>node.start / node.end / token"]
        MEM["MemorySaver<br/>（行程內，隨盒銷毀）"]
    end

    SDK["Zentra SDK — ctx"]
    SDK --> T1["ctx.llm()<br/>base_url=/internal/llm/v1<br/>api_key=run_token"]
    SDK --> T2["ctx.logger → log 事件"]
    SDK --> T3["ctx.emit() → custom 事件"]
    SDK --> T4["ctx.config → manifest 設定"]

    BOOT --> GRAPH
    GRAPH --> HOOK
    GRAPH <--> MEM
    GRAPH --> SDK
    HOOK --> OUT["POST /internal/runs/{id}/events"]
    T1 --> OUT2["POST /internal/llm/v1/chat/completions"]

    style GRAPH fill:#e8eaf6,stroke:#283593,stroke-width:2px
    style SDK fill:#fff3e0,stroke:#e65100,stroke-width:2px
```

**`ctx.llm()` 的關鍵設計**：Internal Gateway 的 LLM 端點做成 **OpenAI 相容**，
所以 `ctx.llm()` 只是一個設了 `base_url` 與 `api_key=run_token` 的標準 LangChain chat model。
SDK 不需要自訂 client，使用者拿到的就是熟悉的 LangChain 介面——**而真金鑰從來沒進過沙盒**（P4）。

**MVP 的 `ctx` 沒有** `tools` / `knowledge` / `secrets`。使用者要工具就在自己的 graph 裡寫
（它們一樣跑在沙盒內、一樣沒有外網），平台級的 Tool Registry 在 MVP 外。

---

## 7. 每個 Agent 的雙門面（API + MCP）

```mermaid
graph TB
    subgraph IN["⬅️ 對外提供（Inbound）— 別人呼叫我"]
        direction TB
        I1["REST<br/>POST /v1/agents/{id}/runs<br/>GET /v1/runs/{id}/events"]
        I2["MCP Server<br/>/mcp/agents/{id}<br/>tools/list · tools/call"]
        I3["OpenAPI 3.1<br/>/v1/agents/{id}/openapi.json"]
    end

    MANIFEST["📋 manifest.entrypoints<br/>（發布時由驗證沙盒抽出）"]
    GEN["Interface Generator<br/>一份定義 → 三種 schema"]
    AGENT["🤖 Agent @ Sandbox<br/>LangGraph Runtime"]

    MANIFEST --> GEN --> I1 & I2 & I3
    I1 & I2 --> AGENT

    style AGENT fill:#fce4ec,stroke:#c2185b,stroke-width:3px
    style GEN fill:#fff9c4,stroke:#f9a825,stroke-width:2px
    style MANIFEST fill:#fff3e0,stroke:#e65100,stroke-width:3px
```

**P3 的實作要求**：`tools/list` 的 input schema 與 REST 的 `input` schema
**必須由同一份 entrypoint 定義生成**，契約測試要驗證兩者等價。

> v0.1 的「Outbound 門面」（Tool Registry / 外接 MCP Client / Agent-as-Tool）在 MVP 外。
> 使用者仍然可以在自己的 Agent 程式碼裡呼叫東西——但**要經過 Internal Gateway，否則網路不通**。

---

## 8. 資料模型（五張表）

```mermaid
erDiagram
    AGENT ||--o{ AGENT_VERSION : versions
    AGENT_VERSION ||--o{ RUN : executes
    RUN ||--o{ RUN_EVENT : emits
    RUN ||--o{ LLM_CALL : meters

    AGENT {
        uuid id PK
        string slug UK
        string name
        string owner_id "單租戶，欄位留著不做 RLS"
        string status "ACTIVE|DELETED"
        timestamp created_at
    }
    AGENT_VERSION {
        uuid id PK
        uuid agent_id FK
        string semver "與 agent_id 唯一"
        string version_id "bundle SHA-256 前 12 碼"
        string status "BUILDING|PUBLISHED|FAILED"
        jsonb manifest
        jsonb entrypoints "驗證沙盒抽出"
        jsonb error
        timestamp published_at
    }
    RUN {
        uuid id PK
        uuid agent_id FK
        uuid agent_version_id FK
        string status "QUEUED|SCHEDULING|RUNNING|SUCCEEDED|FAILED|CANCELLED"
        string trigger "API|MCP|CONSOLE"
        string entrypoint
        string celery_task_id
        string sandbox_id
        jsonb input
        jsonb output
        jsonb error
        jsonb usage
        timestamp created_at
        timestamp started_at
        timestamp finished_at
    }
    RUN_EVENT {
        bigint seq PK "SSE 的 Last-Event-ID"
        uuid run_id FK
        uuid event_id "沙盒事件 UUID v7；run_id 內唯一"
        string type "run.status|node.start|node.end|token|log|custom|error"
        jsonb payload
        timestamp ts
    }
    LLM_CALL {
        uuid id PK
        uuid run_id FK
        string model
        int input_tokens
        int output_tokens
        numeric cost_usd
        int latency_ms
        timestamp ts
    }
```

**為什麼 `run_events` 要落 Postgres 而不只是 Redis Streams**：
SSE 要支援 `Last-Event-ID` 續傳，而且 Run 結束後還要能回頭看事件流——這是 Console 最重要的功能。
Redis Stream 只是即時匯流管道，**持久化的真相在 Postgres**。
沙盒事件另有重送不變的 `event_id`，資料庫限制 `(run_id,event_id)` 唯一；
DB 成功但 Redis 發布失敗時用原 `seq` 重送，SSE 以 `seq` 去重排序（[ADR-0007](../../specs/adr/ADR-0007-run-completion-and-event-retry.md)）。

**沒有的表**（對照 v0.1）：`TENANT`、`USER`、`API_KEY`、`TOOL`、`SKILL`、`FLOW`、
`KNOWLEDGE_COLLECTION`、`DOCUMENT`、`CHUNK`、`EMBEDDING`、`RUN_CHECKPOINT`、`AGENT_ALIAS`、`SANDBOX_INSTANCE`。
它們都在 MVP 外——**不預先建空表**。

---

## 9. 部署拓撲（docker compose）

**四個長駐服務。** Console 沒有自己的容器 —— `api` 用 `StaticFiles` 掛載 `apps/console/`，
再以五條 `FileResponse` 路由給出乾淨網址：

| 網址 | 檔案 |
|---|---|
| `/console/agents` | `agents.html` |
| `/console/agents/{agent_id}` | `agent.html` |
| `/console/agents/{agent_id}/versions/new` | `version-new.html` |
| `/console/runs` | `runs.html` |
| `/console/runs/{run_id}` | `run.html` |

`/` 轉址到 `/console/agents`，靜態資產掛在 `/console/static/*`。
**沒有前端路由器** —— 深層連結、上一頁、404 全部由伺服器處理（[ADR-0006](../../specs/adr/ADR-0006-vanilla-frontend.md)）。

`/internal/*` 與公開 API 共用 `api:8000`；主機映射埠也能到達該路由。
MVP 以綁定 Run 的短效 token 控制存取，並未實作來源 IP 封鎖；
「Internal Gateway」指沙盒唯一可用的網路對端與非公開契約，不代表路由在公開 listener 上不可達。

```mermaid
graph TB
    subgraph HOST["一台主機"]
        subgraph NET_DEF["預設網段 zentra-net"]
            direction LR
            C2["api :8000<br/>REST · MCP · SSE<br/>+ Console 靜態檔"]
            C3["worker"]
            C4[("postgres<br/>:5432")]
            C5[("redis<br/>:6379")]
        end

        subgraph NET_SBX["zentra-sandbox-net — internal: true ⚠️"]
            direction LR
            S1["sandbox-run-abc<br/>（動態，不在 compose）"]
            S2["sandbox-run-def"]
        end

        VOL[("volume: ./data<br/>bundles/ · venvs/ · pgdata/")]
    end

    OUT["🌍 網際網路"]

    C2 --> C4 & C5
    C3 --> C4 & C5
    C3 -->|"docker.sock"| S1 & S2
    S1 & S2 ===|"唯一出口"| C2
    C2 --> OUT
    C2 & C3 --> VOL
    S1 & S2 -.唯讀掛載.-> VOL

    NOTE["⚠️ 三條硬規則<br/>1. api 同時接兩個網段，是沙盒唯一的橋<br/>2. 沙盒網段 internal: true，沒有對外路由<br/>3. 只有 worker 掛 docker.sock，api 不掛"]

    style NET_SBX fill:#fce4ec,stroke:#c2185b,stroke-width:3px
    style NOTE fill:#ffebee,stroke:#c62828,stroke-width:2px
```

### `docker.sock` 的風險，明確記錄

`worker` 需要 `/var/run/docker.sock` 才能動態建沙盒，而**能操作 docker.sock 等同主機 root**。

v0.1 的 ADR-0004 把這條列為「僅限本機開發，正式環境絕對禁止」。
**v0.2.0 的正式環境就是這一套，所以這個風險現在是常駐的。** 誠實記錄，不要假裝它不存在：

| | |
|---|---|
| 現在為什麼可接受 | 內部工具、單租戶、只跑信任的程式碼。能碰到 `worker` 的人本來就有主機權限 |
| 什麼時候不可接受 | 一旦要跑不是自己寫的程式碼，或出現第二個租戶 |
| 到時候怎麼辦 | `docker-socket-proxy` 限縮成只能建刪容器、rootless dockerd，或直接上 k3s（ADR-0005 的首選替代方案） |

---

## 10. 降維對照表（v0.1 → v0.2）

| v0.1 元素 | v0.2 怎麼了 | 理由 |
|---|---|---|
| Kubernetes + Helm + 專用 node pool | → **單機 docker compose** | ADR-0005：內部工具、單租戶、信任程式碼 |
| gVisor (runsc) RuntimeClass | → **移除**，L2 只剩 Docker 原生機制 | 同上。`capabilities()` 誠實回報 |
| RabbitMQ | → **Redis** 同時當 broker + result + Streams | 少一個服務 |
| OpenSearch + pgvector Hybrid Search | → **移除**（知識層整層移出 MVP） | 範圍收斂 |
| MinIO / S3 | → **本機 volume** + `ObjectStore` 介面 | 少一個服務，介面留著 |
| ClickHouse | → Postgres 的 `llm_calls` 表 | 少一個服務 |
| Vault | → compose env / Docker secret | 沙盒本來就拿不到，不需要 Vault |
| Tool Proxy + LLM Proxy + Egress Proxy | → **Internal Gateway**（api 上的 `/internal/*`） | 三個容器併成一組路由，P4 不變 |
| Warm Pool | → **移除** | 共用 runtime image 冷啟已 < 2s |
| per-agent image build + CVE/License 掃描 | → 共用 runtime image + per-version venv 目錄 | 信任程式碼不需要供應鏈掃描 |
| OpenTelemetry + Langfuse + Prometheus | → 結構化 JSON log + `run_events` + `llm_calls` | 單機不需要分散式追蹤 |
| 六條佇列 + Beat + KEDA | → `q.run` + `q.build` | 單機 |
| 平台組裝式 Agent（路徑 A） | → **移出 MVP** | 第二條建立路徑，八週做不完 |
| Agent Flow（React Flow 畫布） | → 原本就是 MVP 外 | |
| HITL / `WAITING_INPUT` / Checkpointer | → **移出 MVP**，狀態機已移除該值 | |
| 多租戶 + RLS | → 單租戶，留 `owner_id` 欄位不做 RLS | |
| L0–L6 七層 | → **四層** L0 接入+控制 / L1 派工 / L2 執行 / L3 資料 | |
| 14 張架構圖 | → **11 張** | 砍掉的東西不需要圖 |
| Next.js + React + TypeScript + Tailwind + shadcn/ui | → **原生 HTML + CSS + ES modules，零建置** | [ADR-0006](../../specs/adr/ADR-0006-vanilla-frontend.md)：五個畫面撐不起這套工具鏈 |
| `console` 容器（第 5 個服務） | → **移除**，由 `api` 的 `StaticFiles` 服務 | 長駐服務 5 → 4 |
| Console BFF（L0 的一個元件） | → **移除**，Console 同源直接打 `/v1` | 不需要 CORS，且 Console 成為 API 契約的第一個消費者 |
| TanStack Query + Zustand | → 一個 ~40 行的 `store.js` | 只有 Run 觀測頁需要共享狀態 |
| Vitest + pnpm + node_modules | → **移除**；Playwright 保留 | 沒有建置步驟就沒有前端建置工具 |

**沒有變的**：五條核心立場 P1–P5、`SandboxDriver` 抽象與 `capabilities()` 誠實原則、
Celery ↔ LangGraph 的判準（ADR-0002）、entrypoints 單一定義生成雙門面（ADR-0003）。
