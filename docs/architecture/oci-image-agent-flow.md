# OCI image 型 Agent：註冊到執行

> 權威定義：[`SPEC.md v0.4.0`](../../specs/SPEC.md)、
> [`ADR-0008`](../../specs/adr/ADR-0008-oci-image-agent-packaging.md)、
> [`ADR-0009`](../../specs/adr/ADR-0009-internal-registry-image-upload.md)、
> [`ZEN-008`](../../specs/features/ZEN-008/spec.md)、[`ZEN-009`](../../specs/features/ZEN-009/spec.md)。
> 本文件只畫清資料流。

## Image 在哪裡，PostgreSQL 存什麼

```mermaid
flowchart LR
    DEV["開發者\ndocker build"] --> REG["既有 OCI Registry\nimage blobs"]
    DEV -->|"push"| REG
    DEV -->|"POST semver + repository@sha256"| API["Zentra API"]
    API --> PG[("PostgreSQL\nagents / agent_versions\nmetadata + digest + schemas")]
    API --> QB{{"Celery q.build"}}
    QB --> BW["build-worker"]
    BW -->|"pull exact digest\nplatform-side credential"| REG
    BW --> CACHE[("VM Docker image cache")]
    BW --> VAL["一次性 validation container"]
    VAL -->|"validation token\nmanifest + entrypoints"| API
    VAL -->|"destroy"| GONE["不存在"]
```

PostgreSQL 不存 image blob，也不存 Registry credential。`image_ref` 必須是 canonical digest reference；
tag 只可出現在開發者自己的 build/push 操作，不進發布身份。

## 上傳路徑（SPEC v0.4.0 / ADR-0009）

第二條進場路徑。它與上面的註冊路徑**在同一個點匯流**：`agent_versions.image_ref`
拿到 canonical `repository@sha256:<manifest digest>`。匯流之後只有一條程式碼路徑。

```mermaid
flowchart LR
    DEV["開發者\ndocker save > agent.tar"] -->|"multipart 上傳"| API["Zentra api"]
    API -->|"串流落檔，不解析 tar"| VOL[("uploads volume\n<agent_version_id>.tar")]
    API --> PG[("PostgreSQL\nBUILDING\nimage_ref = NULL")]
    API --> QB{{"Celery q.build"}}
    QB --> BW["build-worker"]
    VOL -->|"讀 bytes"| BW
    BW -->|"LOAD（交給 daemon）"| D["Docker daemon"]
    D -->|"PUSH localhost:5000"| REG[("內部 Registry\nregistry:2\nzentra-net only")]
    REG -->|"manifest digest"| BW
    BW -->|"寫 image_ref/digest/version_id\n然後刪 tar"| PG
    BW --> VAL["INSPECT → 一次性 validation container"]
    VAL -->|"validation token\nmanifest + entrypoints"| API
```

三件事一定要看懂：

1. **tar 從不進 PostgreSQL**，也不長期留在磁碟上——digest commit 後立刻刪除。
2. **平台行程不解析 tar**。`docker load` 是 daemon 的事；要 image 資訊就 load 完 `inspect`。
3. **push 成功前沒有版本身份**。`docker save`/`load` 會丟掉 registry manifest digest
   （`RepoDigests` 變空），所以 `version_id` 只能在 push 之後產生。
   DB CHECK 保證 `PUBLISHED` 必有完整 digest——沒有可重新 pull 的來源就不是已發布版本（P5）。

內部 Registry 只掛 `zentra-net`，**不掛 `zentra-sandbox-net`**。
`localhost:5000` 是給**主機上的 Docker daemon** 用的名字（daemon 不在 compose 網段上），
沙盒既看不到也連不到它。

## Console、REST、MCP 共用同一條 Run 路徑

```mermaid
sequenceDiagram
    autonumber
    actor U as Console 使用者
    participant X as 外部 REST / MCP Client
    participant API as api / Agent Registry
    participant PG as PostgreSQL
    participant Q as Redis + Celery q.run
    participant W as runner-worker
    participant SM as Sandbox Manager
    participant C as image@sha256 臨時容器
    participant GW as Internal Gateway

    U->>API: 選 Agent/version/entrypoint，POST Run
    X->>API: REST POST Run 或 MCP tools/call
    API->>PG: resolve PUBLISHED version -> exact image digest
    API->>PG: INSERT Run(QUEUED)
    API->>Q: q.run(run_id)
    API-->>U: 202 run_id
    API-->>X: 202 run_id 或同步等待
    Q->>W: 一 Run 一 Celery task
    W->>SM: create SandboxSpec(image_ref@digest)
    SM->>C: non-root + read-only + internal network
    C->>GW: bootstrap / events / result (run token)
    GW->>PG: 持久化事件與結果
    W->>PG: 確認 exit + result，唯一決定終態
    W->>SM: revoke token + destroy
    SM--xC: 容器不存在
```

image 內不提供 REST 或 MCP server，也不暴露 port。兩個門面都在 Zentra `api`；image 裡唯一的執行入口是
`/opt/zentra/bin/zentra-agent run`，它只能透過 Internal Gateway 取 input、送事件與結果。

## 信任與憑證邊界

```mermaid
flowchart TB
    subgraph PLATFORM["平台行程 / VM"]
        CREDS["Registry credential\nLLM provider key"]
        API["api / Internal Gateway"]
        WORKER["worker + Docker API"]
    end
    subgraph SANDBOX["一次性 image container"]
        TOKEN["validation token 或 run token"]
        CODE["使用者 LangGraph code"]
    end
    CREDS --> WORKER
    CREDS --> API
    WORKER -->|"pull image，不轉交 credential"| SANDBOX
    TOKEN -->|"只准存取綁定的 version/run"| API
    CODE --> TOKEN
    SANDBOX -. "無 Registry/DB/Redis/外網路由" .-> CREDS
```

目前仍是「內部工具、只跑自己信任的 image」。digest 只保證內容不可變，不等於內容安全；
沒有 gVisor、image 簽章或 CVE 掃描，不能宣稱可安全執行第三方不可信 image。
