# Zentra Agent Platform — 詳細架構圖

> 本文件是 `Simplified_architecture_diagram.png` 的展開版。
> 規格定義以 [`specs/SPEC.md`](../../specs/SPEC.md) 為準，本文件只負責「畫清楚」。

## 0. 總覽（基礎設施視角）

下圖把本文件的細節收斂成單張實作層級架構圖：接入層、控制平面、Celery 佇列拓撲與 worker 池、
沙盒五層隔離與 Agent Runtime 內部、受控出口，以及知識層、資料層與觀測層。
本文件其餘章節是各區塊的逐項展開。

![Zentra 系統架構](zentra-architecture.svg)


---

## 1. 系統全景圖（Layered Architecture）

對應簡義圖的三大塊：**對外接口** → **Agent Platform** → **文件知識層**。
為了看得清楚，全景拆成「主幹」（本節）與「橫切面」（§1.1 資料與觀測）兩張。

```mermaid
graph TB
    subgraph EXT["🌐 L0 對外接口 / External Consumers"]
        direction LR
        E1["業務系統 A"]
        E2["AI 平台系統"]
        E3["第三方 App / Script"]
        E4["MCP Client<br/>Claude · IDE · 其他 Agent"]
    end

    subgraph GW["🚪 Access Layer 接入層"]
        direction LR
        GW1["REST API Gateway<br/>OpenAPI 3.1"]
        GW2["MCP Gateway<br/>Streamable HTTP"]
        GW3["SSE / WebSocket<br/>事件串流"]
        GW4["Console BFF<br/>Next.js Route Handler"]
    end

    GUARD["🔐 AuthN/AuthZ · Rate Limit · Quota · Tenant Routing"]

    subgraph CTRL["🎛️ L1 控制平面 Control Plane"]
        direction LR
        C1["Agent Service<br/>定義 CRUD · 發布 · Alias"]
        C2["Build Service<br/>uv lock · CVE/License 掃描"]
        C3["Version Manager<br/>Immutable agent@semver"]
        C4["Policy Engine<br/>RBAC/ABAC · Egress 白名單"]
        C5["Secret Manager<br/>Vault · 短期憑證"]
        REG[("Registry 群<br/>Agent · Tool · Skill · Knowledge")]
    end

    subgraph DISP["📮 L2 派工層 Dispatch — Celery"]
        direction LR
        D1{{"RabbitMQ Broker<br/>q.agent · q.build · q.ingest"}}
        D2["Celery Beat<br/>排程"]
        W1["runner-worker<br/>⚠️ 不執行使用者程式碼"]
        W2["build-worker"]
        W3["ingest-worker"]
        D4["Dead Letter Queue"]
    end

    subgraph EXEC["📦 L3 執行平面 Execution — 沙盒"]
        direction TB
        X1["Sandbox Manager<br/>生命週期 · Warm Pool · 配額"]
        subgraph SBX["Sandbox — gVisor，一 Run 一盒"]
            direction LR
            S1["Agent Runtime<br/>LangGraph CompiledStateGraph"]
            S3["使用者 Python Code<br/>唯讀掛載"]
            S4["Agent Server<br/>/invoke · /stream · /mcp"]
        end
        SIDE["🛡️ Sidecar 安全邊界<br/>Tool Proxy · LLM Proxy · Egress Proxy"]
    end

    subgraph KNOW["📚 L4 文件知識層 Knowledge Plane"]
        direction LR
        K1["文件來源<br/>上傳 · 連接器 · 爬取"]
        K2["Ingestion Pipeline<br/>解析 → 切分 → Embedding → 索引"]
        K3[("pgvector<br/>向量")]
        K4[("OpenSearch<br/>BM25")]
        K5["Hybrid Search<br/>RRF 融合 + Rerank"]
    end

    E1 & E2 & E3 --> GW1
    E4 --> GW2
    E1 & E2 --> GW3
    GW1 & GW2 & GW3 & GW4 --> GUARD
    GUARD -->|"管理操作"| C1
    GUARD -->|"建立 Run"| D1

    C1 --- REG
    C1 --> C3
    C2 --> C3
    C4 --> C5
    C2 -->|"送 q.build"| D1

    D2 --> D1
    D1 --> W1 & W2 & W3
    W1 -.重試耗盡.-> D4

    W1 -->|"申請沙盒 · 串流監看"| X1
    X1 -->|"create / destroy"| SBX
    S3 -.載入.-> S1
    S4 --> S1
    S1 --> SIDE
    SIDE -->|"代持憑證呼叫"| REG

    W3 --> K2
    K1 --> K2 --> K3 & K4
    K3 & K4 --> K5
    S1 -->|"ctx.knowledge.search()"| K5

    classDef ext fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
    classDef gw fill:#e8eaf6,stroke:#283593,stroke-width:2px
    classDef ctrl fill:#e0f7fa,stroke:#00838f,stroke-width:2px
    classDef disp fill:#fff8e1,stroke:#ef6c00,stroke-width:2px
    classDef exec fill:#fce4ec,stroke:#c2185b,stroke-width:2px
    classDef know fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    class E1,E2,E3,E4 ext
    class GW1,GW2,GW3,GW4,GUARD gw
    class C1,C2,C3,C4,C5,REG ctrl
    class D1,D2,W1,W2,W3,D4 disp
    class X1,S1,S3,S4,SIDE exec
    class K1,K2,K3,K4,K5 know
```

### 1.1 橫切面：資料層與觀測層

L5 / L6 被**所有層**使用，畫在主幹圖裡會讓線爆炸，因此獨立一張。

```mermaid
graph LR
    subgraph USERS["使用者"]
        direction TB
        U1["L1 控制平面"]
        U2["L2 派工層"]
        U3["L3 執行平面"]
        U4["L4 知識層"]
    end

    subgraph DATA["🗄️ L5 資料層"]
        direction TB
        DB1[("PostgreSQL + pgvector<br/>metadata · runs · checkpoint<br/>全表 Row Level Security")]
        DB2[("Redis<br/>cache · Streams 事件匯流<br/>Celery result backend")]
        DB3[("S3 / MinIO<br/>code bundle · 文件原檔 · artifact")]
        DB4[("ClickHouse<br/>用量 · 稽核分析")]
    end

    subgraph OBS["🔭 L6 觀測層"]
        direction TB
        O1["OpenTelemetry<br/>Trace / Metric / Log"]
        O2["Langfuse<br/>LLM Trace · Token · 成本"]
        O3["Prometheus + Grafana<br/>佇列深度 · 沙盒用量 · SLO"]
        O4["Audit Log<br/>工具呼叫 · 外網請求 · 不可竄改"]
    end

    U1 --> DB1 & DB3
    U2 --> DB2
    U3 -->|"事件 XADD"| DB2
    U3 -->|"checkpoint"| DB1
    U4 --> DB1 & DB3
    USERS -.OTel SDK.-> O1
    U3 -.LLM 呼叫.-> O2
    U2 -.佇列指標.-> O3
    U3 -.Proxy 稽核.-> O4
    O1 & O2 & O3 & O4 --> DB4

    RULE["📏 硬規則：L3 沙盒不得直連 L5<br/>一律經 L1 的 Proxy / SDK"]

    classDef data fill:#f3e5f5,stroke:#6a1b9a,stroke-width:2px
    classDef obs fill:#eceff1,stroke:#455a64,stroke-width:2px
    class DB1,DB2,DB3,DB4 data
    class O1,O2,O3,O4 obs
    style RULE fill:#ffebee,stroke:#c62828,stroke-width:2px
```

---

## 2. 兩種建立 Agent 的路徑

簡義圖上半部「平台建立 Agent」與「上傳 Agent Code」的展開。

```mermaid
flowchart LR
    subgraph PATH_A["路徑 A — 平台組裝（低程式碼）"]
        direction TB
        A1["Console 精靈"]
        A2["勾選 Tools<br/>← Tool Registry"]
        A3["選擇 Skills<br/>← Skill Registry"]
        A4["設定知識庫<br/>← Knowledge Registry"]
        A5["設定 Prompt / Model / 策略"]
        A6["Graph Generator<br/>產出標準 LangGraph"]
        A1 --> A2 --> A3 --> A4 --> A5 --> A6
    end

    subgraph PATH_B["路徑 B — 上傳程式碼（完全彈性）"]
        direction TB
        B1["本機專案<br/>agent.py + zentra.yaml"]
        B2["zentra deploy<br/>打包上傳 bundle"]
        B3["Build Worker<br/>uv lock · 依賴解析"]
        B4["安全掃描<br/>CVE · License · 禁用 API"]
        B5["契約驗證<br/>必須 export build() → StateGraph"]
        B1 --> B2 --> B3 --> B4 --> B5
    end

    MANIFEST["📋 統一 Agent Manifest<br/>agent_id@semver<br/>+ image digest + lockfile"]
    A6 --> MANIFEST
    B5 --> MANIFEST

    MANIFEST --> PUB{"發布檢核<br/>Policy Engine"}
    PUB -->|通過| REG[("Agent Registry<br/>狀態 PUBLISHED")]
    PUB -->|駁回| FAIL["退回 + 原因"]

    REG --> IF1["🔌 自動生成 REST 路由<br/>/v1/agents/{id}/runs"]
    REG --> IF2["🔌 自動生成 MCP 端點<br/>/mcp/agents/{id}"]
    REG --> IF3["🔌 自動生成 OpenAPI Schema"]

    style MANIFEST fill:#fff3e0,stroke:#e65100,stroke-width:3px
    style REG fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    style PUB fill:#fce4ec,stroke:#c2185b,stroke-width:2px
```

**關鍵**：兩條路徑最終收斂到**同一個 Manifest 格式**，下游（派工、沙盒、觀測）完全不需要知道 Agent 是怎麼來的。

---

## 3. 一次 Run 的完整時序（核心流程）

```mermaid
sequenceDiagram
    autonumber
    participant C as 外部系統
    participant GW as API Gateway
    participant API as Agent Service
    participant MQ as RabbitMQ
    participant W as runner-worker<br/>(Celery)
    participant SM as Sandbox Manager
    participant SB as Sandbox<br/>(LangGraph Runtime)
    participant PX as Tool/LLM/Egress Proxy
    participant R as Redis Streams
    participant PG as Postgres

    C->>GW: POST /v1/agents/{id}/runs
    GW->>GW: 驗 token · scope · 配額
    GW->>API: 建立 Run
    API->>PG: INSERT runs (QUEUED)
    API->>MQ: send_task(q.agent.short, run_id)
    API-->>C: 202 { run_id }

    C->>GW: GET /v1/runs/{id}/events (SSE)
    GW->>R: XREAD run:{id}

    MQ->>W: 取件 (acks_late, prefetch=1)
    W->>PG: UPDATE SCHEDULING
    W->>SM: acquire(agent@ver, limits)
    Note over SM: Warm Pool 命中 → ≤800ms<br/>未命中 → 冷啟 ≤3s
    SM->>SB: 啟動（gVisor · 唯讀 code · tmpfs）
    SM->>SB: 注入短期 token（TTL 60s）
    SB->>SB: import build() → compile graph
    W->>PG: UPDATE RUNNING

    loop 每個 LangGraph node
        SB->>SB: 執行 node
        SB->>PX: LLM / Tool / Knowledge 呼叫
        PX->>PX: 換憑證 · 白名單檢查 · 稽核
        PX-->>SB: 結果
        SB->>R: XADD 事件 (token / node / tool)
        R-->>GW: 串流
        GW-->>C: SSE event
        SB->>PG: checkpoint (LangGraph state)
    end

    alt 需要人工介入 (HITL)
        SB->>PG: UPDATE WAITING_INPUT
        SB->>SM: 掛起（保留 checkpoint，釋放算力）
        C->>GW: POST /v1/runs/{id}/resume
        GW->>MQ: 重新派工（從 checkpoint 續跑）
    end

    SB-->>W: 最終結果 + 用量
    W->>PG: UPDATE SUCCEEDED + usage
    W->>SM: release → 銷毀沙盒
    SM->>SB: destroy（不重用）
    R-->>C: SSE done
```

**安全重點（§P1）**：`runner-worker` 全程只做「申請沙盒、轉送事件、回寫狀態」，
**從來沒有在自己的行程裡 import 或 exec 使用者的程式碼**。

---

## 4. 沙盒隔離模型（核心技術 1）

```mermaid
graph TB
    subgraph HOST["宿主節點 (Kubernetes Node · 專用 node pool)"]
        subgraph POD["Sandbox Pod — RuntimeClass: gvisor"]
            subgraph L5["L5 應用層限制"]
                A1["Zentra SDK 攔截層<br/>禁自建 LLM client<br/>禁裸 socket"]
                A2["Import Hook<br/>黑名單模組阻擋"]
            end
            subgraph L4["L4 檔案系統"]
                F1["/app 唯讀<br/>使用者 code + venv"]
                F2["/workspace tmpfs rw<br/>上限 512MB · 隨盒銷毀"]
                F3["無 hostPath · 無 docker.sock"]
            end
            subgraph L3["L3 資源限制 (cgroups v2)"]
                RC["CPU 2 core · Mem 4GB<br/>pids 256 · 無 swap<br/>wall-clock timeout<br/>stdout 上限 10MB"]
            end
            subgraph L2["L2 核心隔離"]
                K1["gVisor runsc<br/>使用者態核心"]
                K2["seccomp-bpf 白名單<br/>drop ALL capabilities<br/>no-new-privileges<br/>non-root UID 65534"]
            end
            subgraph L1["L1 網路隔離"]
                N1["NetworkPolicy: default deny"]
                N2["封鎖 169.254.169.254<br/>(雲端 metadata)"]
                N3["封鎖內網 CIDR"]
                N4["僅允許 → Egress Proxy"]
            end
        end
        EP["🛡️ Egress Proxy<br/>網域白名單 · TLS 檢查 · 全稽核"]
        TP["🛡️ Tool Proxy<br/>代持憑證 · 代呼叫 · 限流"]
        LP["🛡️ LLM Proxy<br/>模型路由 · Token 計量"]
    end

    POD -->|"唯一出口"| EP
    POD -->|"ctx.tools.*"| TP
    POD -->|"ctx.llm()"| LP
    EP --> NET["🌍 外部網路<br/>（僅白名單網域）"]
    TP --> NET
    LP --> NET

    SEC[("🔐 Vault")] -.->|"長期憑證<br/>只到 Proxy"| TP & LP
    SEC -.->|"短期 token TTL 60s"| POD

    AUD[("📝 Audit Log")]
    EP & TP & LP --> AUD

    style POD fill:#fce4ec,stroke:#c2185b,stroke-width:3px
    style EP fill:#fff3e0,stroke:#e65100,stroke-width:2px
    style TP fill:#fff3e0,stroke:#e65100,stroke-width:2px
    style LP fill:#fff3e0,stroke:#e65100,stroke-width:2px
    style SEC fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
```

### 沙盒生命週期

```mermaid
stateDiagram-v2
    [*] --> Warm: Warm Pool 預熱<br/>(常用 image 預先啟動)
    Warm --> Claimed: runner-worker 申請
    [*] --> ColdStart: Pool 未命中
    ColdStart --> Claimed: 拉 image + 啟動 (≤3s)
    Claimed --> Initializing: 注入短期 token<br/>掛載 code (ro)
    Initializing --> Running: build() → compile graph
    Running --> Suspended: HITL 中斷<br/>(存 checkpoint · 釋放算力)
    Suspended --> Running: resume（可換新沙盒）
    Running --> Draining: 完成 / 失敗 / 逾時
    Draining --> Destroyed: 回收 tmpfs · 撤銷 token
    Destroyed --> [*]

    note right of Destroyed
        沙盒永不重用於不同 Run
        避免跨租戶狀態殘留
    end note
```

---

## 5. Celery 派工拓撲（核心技術 2）

```mermaid
graph LR
    subgraph PROD["任務生產者"]
        S1["API Gateway<br/>使用者觸發"]
        S2["Celery Beat<br/>排程 Agent"]
        S3["Webhook / Event 觸發"]
        S4["Agent Flow<br/>節點展開"]
    end

    subgraph BROKER["RabbitMQ — 佇列拓撲"]
        direction TB
        Q1["q.agent.short<br/>≤ 60s · 高併發"]
        Q2["q.agent.long<br/>≤ 6h · prefetch=1"]
        Q3["q.agent.priority<br/>付費租戶優先"]
        Q4["q.build<br/>打包 · 依賴解析"]
        Q5["q.ingest<br/>文件處理"]
        Q6["q.eval<br/>QA 回歸評測"]
        DLQ["☠️ q.dead_letter<br/>重試耗盡"]
    end

    subgraph WORKERS["Worker Pools（獨立擴縮）"]
        direction TB
        WA["runner-worker × N<br/>--concurrency=8<br/>acks_late=True"]
        WB["runner-worker-long × M<br/>--concurrency=1"]
        WC["build-worker × K<br/>需較大磁碟"]
        WD["ingest-worker × J<br/>CPU/GPU 密集"]
        WE["eval-worker"]
    end

    S1 --> Q1 & Q2 & Q3
    S2 --> Q1
    S3 --> Q1
    S4 --> Q1
    S1 --> Q4 & Q5

    Q1 & Q3 --> WA
    Q2 --> WB
    Q4 --> WC
    Q5 --> WD
    Q6 --> WE

    WA & WB --> SM["Sandbox Manager"]
    WA & WB -.重試耗盡.-> DLQ
    DLQ --> ALERT["🔔 告警 + 人工重放"]

    RES[("Redis<br/>Result Backend")]
    WA & WB & WC & WD & WE --> RES

    KEDA["KEDA Autoscaler<br/>依佇列長度擴縮"] -.監看.-> BROKER
    KEDA -.擴縮.-> WORKERS

    style DLQ fill:#ffebee,stroke:#c62828,stroke-width:2px
    style SM fill:#fce4ec,stroke:#c2185b,stroke-width:2px
    style KEDA fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
```

### Celery 與 LangGraph 的職責邊界（常見混淆點）

```mermaid
graph TB
    subgraph CEL["Celery 負責 — 跨行程 / 跨機器"]
        C1["佇列與優先權"]
        C2["Worker 擴縮 · 資源隔離"]
        C3["重試 · 逾時 · 死信"]
        C4["排程 (Beat)"]
        C5["多 Agent 之間的 fan-out/fan-in"]
    end
    subgraph LG["LangGraph 負責 — 單一 Agent 行程內"]
        L1["節點狀態機與條件分支"]
        L2["Checkpoint 與續跑"]
        L3["HITL interrupt"]
        L4["串流 token / 事件"]
        L5["子圖與工具迴圈"]
    end
    RULE["📏 判準：<br/>需要跨機器 / 需要排隊 / 需要獨立擴縮 → Celery<br/>只是同一 Agent 內部的下一步 → LangGraph node"]
    CEL --> RULE
    LG --> RULE
    style RULE fill:#fff9c4,stroke:#f9a825,stroke-width:3px
```

---

## 6. Agent Runtime 內部（LangChain + LangGraph）

```mermaid
graph TB
    subgraph RT["Agent Runtime（沙盒內唯一入口）"]
        BOOT["Bootstrap<br/>載入 manifest → import build() → compile"]
        subgraph GRAPH["CompiledStateGraph"]
            direction TB
            N0(["START"])
            N1["plan / route"]
            N2{"需要工具?"}
            N3["tool_node<br/>(ToolNode)"]
            N4["knowledge_node<br/>Hybrid Search"]
            N5["llm_node<br/>ctx.llm()"]
            N6{"需要人工確認?"}
            N7["interrupt()<br/>HITL"]
            N8["finalize"]
            N9(["END"])
            N0 --> N1 --> N2
            N2 -->|是| N3 --> N5
            N2 -->|需知識| N4 --> N5
            N2 -->|否| N5
            N5 --> N6
            N6 -->|是| N7 -.resume.-> N5
            N6 -->|否| N8 --> N9
        end
        CKPT["Checkpointer<br/>PostgresSaver"]
        STORE["Store<br/>長期記憶 (跨 Run)"]
        SRV["Agent Server (FastAPI)<br/>/invoke · /stream · /mcp · /healthz"]
    end

    SDK["Zentra SDK — ctx"]
    SDK --> T1["ctx.llm() → LLM Proxy"]
    SDK --> T2["ctx.tools → Tool Proxy"]
    SDK --> T3["ctx.knowledge → Hybrid Search"]
    SDK --> T4["ctx.secrets → 短期憑證"]
    SDK --> T5["ctx.emit() → Redis Streams"]

    BOOT --> GRAPH
    GRAPH <--> CKPT
    GRAPH <--> STORE
    GRAPH --> SDK
    SRV --> GRAPH

    N3 -.-> T2
    N4 -.-> T3
    N5 -.-> T1

    style GRAPH fill:#e8eaf6,stroke:#283593,stroke-width:2px
    style SDK fill:#fff3e0,stroke:#e65100,stroke-width:2px
    style SRV fill:#e0f7fa,stroke:#00838f,stroke-width:2px
```

---

## 7. 每個 Agent 的雙門面（API + MCP）

簡義圖中每個 Agent 卡片的「外接API / 外接MCP」——**雙向**都要通。

```mermaid
graph TB
    subgraph IN["⬅️ 對外提供（Inbound）— 別人呼叫我"]
        direction TB
        I1["REST<br/>POST /v1/agents/{id}/runs<br/>GET /v1/runs/{id}/events"]
        I2["MCP Server<br/>/mcp/agents/{id}<br/>tools/list · tools/call · resources"]
        I3["OpenAPI 3.1 Schema<br/>自動生成 · 可直接餵給別的 Agent"]
    end

    AGENT["🤖 Agent @ Sandbox<br/>LangGraph Runtime"]

    subgraph OUT["➡️ 對外消費（Outbound）— 我呼叫別人"]
        direction TB
        O1["外接 API<br/>Tool Registry 的 HTTP Tool"]
        O2["外接 MCP Client<br/>掛載第三方 MCP Server"]
        O3["平台內建 Tools"]
        O4["知識庫 Hybrid Search"]
        O5["其他 Zentra Agent<br/>(Agent-as-Tool)"]
    end

    GEN["Interface Generator<br/>讀 manifest 的 entrypoints<br/>同時產出 REST + MCP schema"]
    GEN --> I1 & I2 & I3
    I1 & I2 --> AGENT
    AGENT --> O1 & O2 & O3 & O4 & O5
    O1 & O2 --> PROXY["Tool Proxy<br/>憑證 · 限流 · 稽核"] --> WORLD["🌍 外部服務"]
    O5 -.遞迴.-> AGENT

    style AGENT fill:#fce4ec,stroke:#c2185b,stroke-width:3px
    style GEN fill:#fff9c4,stroke:#f9a825,stroke-width:2px
    style PROXY fill:#fff3e0,stroke:#e65100,stroke-width:2px
```

**設計重點**：`entrypoints` 在 manifest 宣告一次，REST schema 與 MCP tool schema 由同一份定義生成，
**永遠不會有 API 跟 MCP 行為不一致的問題**。

---

## 8. 文件知識層（Ingestion → Hybrid Search）

```mermaid
flowchart TB
    subgraph SRC["文件來源"]
        direction LR
        U1["Console 上傳<br/>PDF/Word/PPT/MD"]
        U2["連接器<br/>SharePoint · S3 · Confluence"]
        U3["API 推送"]
        U4["網頁爬取"]
    end

    RAW[("S3 / MinIO<br/>原始檔保存")]
    SRC --> RAW --> Q["q.ingest<br/>(Celery)"]

    subgraph PIPE["Ingestion Pipeline（ingest-worker）"]
        direction TB
        P1["1. 解析 Parse<br/>版面辨識 · 表格 · OCR"]
        P2["2. 正規化<br/>→ Markdown + 結構中繼資料"]
        P3["3. 切分 Chunk<br/>語意切分 + 父子分塊"]
        P4["4. 擴充<br/>摘要 · 關鍵詞 · 假設問題"]
        P5["5. Embedding<br/>批次向量化"]
        P6["6. 索引 Index"]
        P1 --> P2 --> P3 --> P4 --> P5 --> P6
    end
    Q --> PIPE

    P6 --> VDB[("pgvector<br/>向量索引 HNSW")]
    P6 --> KDB[("OpenSearch<br/>BM25 全文")]
    P6 --> META[("Postgres<br/>chunk 中繼 · ACL")]

    subgraph SEARCH["Hybrid Search（查詢時）"]
        direction TB
        H0["Query 改寫<br/>多查詢展開 · HyDE"]
        H1["向量召回 top-k"]
        H2["關鍵詞召回 top-k"]
        H3["RRF 融合"]
        H4["Rerank<br/>Cross-Encoder"]
        H5["ACL 過濾<br/>tenant + 文件權限"]
        H6["父分塊還原<br/>回傳完整上下文"]
        H0 --> H1 & H2
        H1 & H2 --> H3 --> H4 --> H5 --> H6
    end

    VDB --> H1
    KDB --> H2
    META --> H5

    AG["🤖 Agent<br/>ctx.knowledge.search()"]
    AG --> H0
    H6 --> AG

    KREG[("Knowledge Registry<br/>Agent ↔ Collection 綁定")]
    KREG -.限定可查範圍.-> H5

    style PIPE fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    style SEARCH fill:#e0f7fa,stroke:#00838f,stroke-width:2px
    style AG fill:#fce4ec,stroke:#c2185b,stroke-width:2px
```

---

## 9. Agent Flow — 多 Agent 編排（選配）

```mermaid
graph TB
    subgraph FLOW["Agent Flow 定義（React Flow 畫布 → Flow DAG）"]
        direction LR
        FN1["Agent A<br/>資料擷取"]
        FN2["Agent B<br/>分析"]
        FN3["Agent C<br/>產出報告"]
        FC{"條件分支"}
        FP["平行 fan-out"]
        FN1 --> FC
        FC -->|路徑1| FN2
        FC -->|路徑2| FP
        FP --> FN2 & FN3
        FN2 --> FN3
    end

    COMP["Flow Compiler<br/>DAG → LangGraph Supervisor Graph"]
    FLOW --> COMP

    subgraph EXECM["兩種執行模式"]
        direction TB
        M1["模式 1：同盒執行<br/>輕量 Agent 編成單一 supervisor graph<br/>✅ 低延遲 ❌ 共用資源"]
        M2["模式 2：跨盒執行<br/>每個 Agent 一個沙盒<br/>Celery chord 做 fan-out/fan-in<br/>✅ 強隔離 · 獨立擴縮 ❌ 較高延遲"]
    end
    COMP --> M1 & M2

    SHARED[("Flow State<br/>Postgres Checkpoint<br/>跨節點共享狀態")]
    M1 & M2 <--> SHARED

    style COMP fill:#fff9c4,stroke:#f9a825,stroke-width:2px
    style M2 fill:#fce4ec,stroke:#c2185b,stroke-width:2px
```

**選擇準則**：Agent 來自不同租戶 / 含上傳程式碼 / 需獨立擴縮 → **模式 2**（預設）。純平台組裝且信任邊界相同 → 模式 1。

---

## 10. 資料模型（ER 核心）

```mermaid
erDiagram
    TENANT ||--o{ USER : has
    TENANT ||--o{ AGENT : owns
    TENANT ||--o{ KNOWLEDGE_COLLECTION : owns
    TENANT ||--o{ API_KEY : issues

    AGENT ||--o{ AGENT_VERSION : versions
    AGENT ||--o{ AGENT_ALIAS : routes
    AGENT_VERSION ||--|| CODE_BUNDLE : packages
    AGENT_VERSION ||--o{ AGENT_TOOL_BINDING : binds
    AGENT_VERSION ||--o{ AGENT_KNOWLEDGE_BINDING : binds
    AGENT_VERSION ||--o{ ENTRYPOINT : exposes

    TOOL ||--o{ AGENT_TOOL_BINDING : used_by
    TOOL ||--o| TOOL_CREDENTIAL : uses
    SKILL ||--o{ AGENT_VERSION : composed_into

    AGENT_VERSION ||--o{ RUN : executes
    RUN ||--o{ RUN_EVENT : emits
    RUN ||--o{ RUN_CHECKPOINT : saves
    RUN ||--o{ TOOL_CALL : invokes
    RUN ||--|| USAGE_RECORD : meters
    RUN ||--o| SANDBOX_INSTANCE : runs_in

    KNOWLEDGE_COLLECTION ||--o{ DOCUMENT : contains
    DOCUMENT ||--o{ CHUNK : split_into
    CHUNK ||--|| EMBEDDING : vectorized

    FLOW ||--o{ FLOW_NODE : contains
    FLOW_NODE }o--|| AGENT_VERSION : references
    FLOW ||--o{ RUN : orchestrates

    AGENT {
        uuid id PK
        uuid tenant_id FK
        string slug
        string source_type "BUILT | UPLOADED"
        string status
    }
    AGENT_VERSION {
        uuid id PK
        string semver
        string image_digest
        jsonb manifest
        string status "BUILDING|PUBLISHED|DEPRECATED"
    }
    RUN {
        uuid id PK
        uuid tenant_id FK
        string status "QUEUED|SCHEDULING|RUNNING|WAITING_INPUT|SUCCEEDED|FAILED|CANCELLED"
        string trigger "API|MCP|SCHEDULE|FLOW"
        string celery_task_id
        jsonb input
        jsonb output
        jsonb error
    }
    SANDBOX_INSTANCE {
        uuid id PK
        string runtime_class "gvisor"
        jsonb resource_limits
        timestamp destroyed_at
    }
```

> 所有含 `tenant_id` 的表一律啟用 PostgreSQL Row Level Security，應用層過濾只是第一道防線。

---

## 11. 部署拓撲（Kubernetes）

```mermaid
graph TB
    subgraph EDGE["邊緣"]
        CDN["CDN"] --> ING["Ingress / WAF"]
    end

    subgraph NS_SYS["namespace: zentra-system（一般 runtime）"]
        direction LR
        D1["deploy: api-gateway"]
        D2["deploy: mcp-gateway"]
        D3["deploy: agent-service"]
        D4["deploy: console (Next.js)"]
        D5["deploy: sandbox-manager"]
        D6["deploy: tool-proxy"]
        D7["deploy: llm-proxy"]
        D8["deploy: egress-proxy"]
    end

    subgraph NS_WK["namespace: zentra-workers"]
        direction LR
        WK1["deploy: runner-worker (HPA/KEDA)"]
        WK2["deploy: build-worker"]
        WK3["deploy: ingest-worker"]
        WK4["cronjob: celery-beat"]
    end

    subgraph NS_SBX["namespace: zentra-sandbox ⚠️ 隔離 node pool"]
        direction LR
        SB1["pod: sandbox-*<br/>RuntimeClass=gvisor<br/>NetworkPolicy=deny-all"]
        SB2["Warm Pool<br/>預熱常用 image"]
    end

    subgraph STATE["有狀態服務（託管或 Operator）"]
        direction LR
        ST1[("PostgreSQL + pgvector")]
        ST2[("Redis")]
        ST3[("RabbitMQ")]
        ST4[("OpenSearch")]
        ST5[("MinIO / S3")]
        ST6[("Vault")]
    end

    ING --> D1 & D2 & D4
    D1 & D2 --> D3
    D3 --> ST3
    ST3 --> WK1 & WK2 & WK3
    WK1 --> D5 --> SB1
    SB1 --> D6 & D7 & D8
    D3 & D5 & D6 & D7 --> ST1 & ST2
    WK3 --> ST1 & ST4 & ST5
    D6 & D7 --> ST6

    NOTE["⚠️ sandbox namespace 三條硬規則：<br/>1. 專用 node pool（taint/toleration），不與平台元件同機<br/>2. RuntimeClass=gvisor，不可降級為 runc<br/>3. NetworkPolicy 預設 deny，只開 egress-proxy 一條路"]

    style NS_SBX fill:#fce4ec,stroke:#c2185b,stroke-width:3px
    style NOTE fill:#ffebee,stroke:#c62828,stroke-width:2px
```

---

## 12. 與簡義圖的對照表

| 簡義圖元素 | 詳細架構對應 | 章節 |
|---|---|---|
| 對外接口 / MCP 接口 / REST API 接口 | Access Layer：MCP Gateway + REST Gateway + Auth + Rate Limit | §1, §7 |
| 平台建立 Agent（勾選 Tools / Skills / 知識庫） | 路徑 A：Console 精靈 → Graph Generator | §2 |
| 上傳 Agent Code（Python Package / 第三方套件） | 路徑 B：CLI → Build Worker → 掃描 → 契約驗證 | §2 |
| Agent A/B/C 的 Runtime | Sandbox 內的 LangGraph CompiledStateGraph | §6 |
| Agent 卡片的 外接API / 外接MCP | 雙門面模型（Inbound + Outbound） | §7 |
| Agent Flow（選配） | Flow Compiler → supervisor graph / Celery chord | §9 |
| Tool Registry / Skill Registry | 控制平面 Registry 群 + Tool Proxy | §1 |
| 平台基礎能力 · 隔離執行 | 沙盒五層隔離模型 | §4 |
| 平台基礎能力 · 權限與 Secret | Policy Engine + Vault + 短期憑證 | §1, §4 |
| 平台基礎能力 · Log·Trace | 觀測層 OTel + Langfuse + Audit Log | §1 |
| 平台基礎能力 · 版本管理 | Version Manager（immutable agent@semver + alias） | §1, §2 |
| 文件知識層（解析·切分·Embedding·索引） | Ingestion Pipeline 六階段 | §8 |
| Hybrid Search（Vector + Keyword） | RRF 融合 + Rerank + ACL 過濾 | §8 |
| （簡義圖未畫，但必須有） | Celery 派工層、Sandbox Manager、三個 Proxy、資料層、觀測層 | §1, §5 |
