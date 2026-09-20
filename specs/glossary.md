# 詞彙表 (Glossary)

> **全隊用同一組字。** 規格、程式碼、UI 文案、測試名稱都引用這裡的用詞。
> 發現有人用了不同說法 → 開 `SPEC-QUESTION` 統一，不要並存兩種叫法。
>
> **v0.2.0 降維後**（[ADR-0005](./adr/ADR-0005-mvp-single-host-scope.md)）：
> 移出 MVP 的詞收在最後一節 §5，**不要在程式碼或 UI 裡使用它們**。

## 1. 核心概念

| 詞 | 英文 / 程式碼識別 | 定義 | 不要說成 |
|---|---|---|---|
| **Agent** | `agent` | 一個可被呼叫的智能體定義，由 manifest + code bundle 構成 | bot、assistant、model |
| **Agent 版本** | `agent_version`, `agent@semver` | Agent 的不可變快照。**執行的最小單位**，發布後凍結 | revision、build |
| **Version ID** | `version_id` | bundle 的 **SHA-256 前 12 碼**。P5 的識別依據，取代原本的 image digest | hash、digest、tag |
| **Manifest** | `manifest` | Agent 的宣告式定義：entrypoints、依賴、資源限制、模型設定 | config、metadata |
| **Entrypoint** | `entrypoint` | Agent 對外暴露的一個可呼叫入口。**同一份定義同時生成 REST schema 與 MCP tool schema** | endpoint、function |
| **Code Bundle** | `bundle` | 使用者上傳的 `.tar.gz`，解壓後唯讀掛進沙盒的 `/app/code` | package、artifact、zip |
| **Run** | `run`, `run_id` | 一次 Agent 執行實例。有狀態機（SPEC §9）與事件流 | job、execution、session |
| **Run 事件** | `run_event` | Run 過程中推送的單一事件（`node.start`、`token`、`log`…） | log、message |
| **Sandbox** | `sandbox` | 執行 Agent 程式碼的隔離容器。**一 Run 一盒，結束即銷毀，絕不重用** | container、vm、worker |

## 2. 平台元件

| 詞 | 定義 |
|---|---|
| **Sandbox Manager** | 管理沙盒生命週期與併發配額。**它建立沙盒，不執行程式碼** |
| **SandboxDriver** | 沙盒的建立介面。MVP 只有 `DockerSandboxDriver`。`capabilities()` 必須誠實回報提供哪幾層隔離 |
| **runner-worker** | Celery worker，負責申請沙盒、回寫狀態。**它不執行使用者程式碼**（P1） |
| **build-worker** | Celery worker，負責解壓 bundle、裝 venv、起驗證沙盒。**解析依賴也在容器內做**（P1） |
| **Internal Gateway** | `api` 服務上的一組 `/internal/*` 路由，**沙盒唯一的網路對端**。代持 LLM 憑證、收事件、發 input。取代原本的 Tool / LLM / Egress 三個 Proxy |
| **Run Token** | 注入沙盒的憑證。綁定單一 `run_id`，TTL = 該 Run 的 timeout，跑完撤銷。**只能對 Internal Gateway 使用** |
| **Interface Generator** | 讀 manifest 的 entrypoints，同時產出 REST + MCP schema（P3 的實作） |
| **ObjectStore** | bundle 與 venv 的儲存介面。MVP 實作是 `LocalObjectStore`（本機 volume），之後可換 `S3ObjectStore` |

## 3. 容易搞混的區別

| A | B | 差在哪 |
|---|---|---|
| **外接 API** | **對外 API** | **外接**＝Agent 呼叫別人（Outbound）；**對外**＝別人呼叫 Agent（Inbound）。MCP 同理 |
| **Celery task** | **LangGraph node** | Celery 管跨行程排隊與重試；LangGraph 管單一 Agent 行程內的狀態機。判準見 [ADR-0002](./adr/ADR-0002-celery-langgraph-boundary.md) |
| **`agent_id`** | **`agent_version_id`** | 前者是長期識別；後者是實際執行的版本。**Run 記錄的是後者** |
| **`semver`** | **`version_id`** | `semver` 是人給的版號（`1.2.0`）；`version_id` 是 bundle 內容的 SHA-256。兩者一起構成版本識別 |
| **`FAILED`** | **`CANCELLED`** | 前者是非預期失敗（含逾時、沙盒錯誤、使用者程式碼例外，用 `error.code` 區分）；後者是使用者主動取消 |
| **`BUILDING`** | **`SCHEDULING`** | 前者是**版本**在發布中；後者是**Run** 在等沙盒。兩個不同的狀態機，不要混用 |

## 4. 隔離與憑證

| 詞 | 定義 |
|---|---|
| **四層隔離** | L1 網路 / L2 核心 / L3 資源 / L4 檔案（+ L5 SDK 攔截）。定義在 SPEC §6.2 |
| **L2 核心隔離** | MVP **只有 Docker 原生機制**（cap_drop、non-root、預設 seccomp），**沒有 gVisor**。`capabilities()` 必須如實回報 |
| **沙盒網段** | `zentra-sandbox-net`，Docker `internal: true`，**無對外路由**。`api` 同時接上它與預設網段，成為唯一橋樑 |
| **API Key** | 外部系統呼叫 `/v1/*` 用的長期憑證。**只存在平台側，不進沙盒** |

## 5. MVP 外的詞（架構留位，**現在不要用**）

這些詞在 v0.1 的規格裡出現過，v0.2.0 移出 MVP（[SPEC §11](./SPEC.md)）。
**不要在程式碼、資料表、UI 文案或測試名稱裡使用它們**，否則就是在建沒人用的抽象。

| 詞 | 原本的意思 | 為什麼移出 |
|---|---|---|
| **平台組裝 / `source_type=BUILT`** | 在 Console 勾選 Tools 產出 LangGraph | 第二條建立路徑，八週做不完。MVP 只有 `UPLOADED` 一種，因此連 `source_type` 欄位都不建 |
| **Tool / Tool Registry / Tool Proxy** | 可註冊的外部能力與其憑證代理 | 使用者可以自己在 Agent 程式碼裡寫工具；平台級 Registry 不在 MVP |
| **Skill / Skill Registry** | 封裝好的 prompt + tool + 子圖 | 同上 |
| **知識集合 / Chunk / Hybrid Search / Ingestion** | 文件檢索 | 整個知識層移出 MVP |
| **Flow / Flow Run** | 多 Agent 編排 DAG | 原本就是 MVP 外 |
| **Alias** | 指向某版本的可變指標 | 不指定 `version` 即用最新 `PUBLISHED`，夠用 |
| **Tenant / `tenant_id` / RLS** | 多租戶隔離 | 單租戶。資料表留 `owner_id` 欄位，但不做 RLS |
| **Warm Pool** | 預熱沙盒降低冷啟 | 共用 runtime image 的冷啟已 < 2s |
| **HITL / `WAITING_INPUT` / Checkpointer** | 中斷後續跑 | 狀態機已移除 `WAITING_INPUT`。MVP 的 LangGraph 用行程內 `MemorySaver` |
