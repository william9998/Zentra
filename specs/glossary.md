# 詞彙表 (Glossary)

> **全隊用同一組字。** 規格、程式碼、UI 文案、測試名稱都引用這裡的用詞。
> 發現有人用了不同說法 → 開 `SPEC-QUESTION` 統一，不要並存兩種叫法。

## 核心概念

| 詞 | 英文 / 程式碼識別 | 定義 | 不要說成 |
|---|---|---|---|
| **Agent** | `agent` | 一個可被呼叫的智能體定義，由 manifest + code bundle（或平台組裝設定）構成 | bot、assistant、model |
| **Agent 版本** | `agent_version`, `agent@semver` | Agent 的不可變快照。**執行的最小單位**，發布後凍結 | revision、build |
| **Alias** | `alias` | 指向某個 agent version 的可變指標（如 `prod` → `v1.2.0`），流量切換用 | tag、channel |
| **Manifest** | `manifest` | Agent 的宣告式定義：entrypoints、tools、knowledge、資源限制、模型設定 | config、metadata |
| **Entrypoint** | `entrypoint` | Agent 對外暴露的一個可呼叫入口。**同一份定義同時生成 REST schema 與 MCP tool schema** | endpoint、function |
| **Run** | `run`, `run_id` | 一次 Agent 執行實例。有狀態機（SPEC §9）與事件流 | job、execution、session |
| **Run 事件** | `run_event` | Run 過程中推送的單一事件（token、節點進入/離開、工具呼叫） | log、message |
| **Sandbox** | `sandbox` | 執行 Agent 程式碼的隔離環境。**一 Run 一盒，結束即銷毀，絕不重用** | container、vm、worker |
| **Tool** | `tool` | Agent 可呼叫的外部能力（HTTP API / Python function / 外接 MCP server） | function、plugin、skill |
| **Skill** | `skill` | 封裝好的 prompt + tool + 子圖複用單元（選配功能） | template、preset |
| **Flow** | `flow` | 多個 Agent 組成的編排 DAG（Agent Flow） | pipeline、workflow、chain |
| **知識集合** | `knowledge_collection` | 一組文件的容器，Agent 綁定它來做檢索 | index、dataset、KB |
| **Chunk** | `chunk` | 文件切分後的最小檢索單位 | segment、passage |

## 兩種建立方式（不要混用叫法）

| 詞 | 程式碼識別 | 定義 |
|---|---|---|
| **平台組裝** | `source_type = "BUILT"` | 在 Console 勾選 Tools / Skills / 知識庫，由 Graph Generator 產出標準 LangGraph |
| **上傳程式碼** | `source_type = "UPLOADED"` | 開發者寫 Python，`zentra deploy` 上傳，經 Build Service 打包 |

兩者最終收斂到**同一個 Manifest 格式**，下游不需區分。

## 平台元件

| 詞 | 定義 |
|---|---|
| **Sandbox Manager** | 管理沙盒生命週期、Warm Pool、配額。**它建立沙盒，不執行程式碼** |
| **runner-worker** | Celery worker，負責申請沙盒、轉送事件、回寫狀態。**它不執行使用者程式碼**（P1） |
| **Tool Proxy** | 代持工具憑證、代呼叫、留稽核。沙盒只送「呼叫哪個 tool + 參數」 |
| **LLM Proxy** | 統一模型路由與 token 計量。沙盒拿不到模型供應商的 key |
| **Egress Proxy** | 沙盒對外網的唯一出口，預設拒絕，網域白名單 |
| **Interface Generator** | 讀 manifest 的 entrypoints，同時產出 REST + MCP schema（P3 的實作） |
| **Build Service** | 打包上傳的程式碼：`uv lock` → CVE/License 掃描 → 契約驗證 → image digest |
| **Checkpointer** | LangGraph 狀態持久化，支援 HITL 中斷後續跑 |
| **Hybrid Search** | 向量 + 關鍵詞雙路召回，RRF 融合後 Rerank |

## 容易搞混的區別

| A | B | 差在哪 |
|---|---|---|
| **Tool** | **Skill** | Tool 是單一能力（一個 API 呼叫）；Skill 是封裝好的組合（prompt + 多個 tool + 子圖） |
| **Run** | **Flow Run** | Run 是單一 Agent 執行；Flow Run 是編排多個 Agent，會展開成多個子 Run |
| **外接 API** | **對外 API** | **外接**＝Agent 呼叫別人（Outbound）；**對外**＝別人呼叫 Agent（Inbound）。MCP 同理 |
| **Celery task** | **LangGraph node** | Celery 管跨行程排隊與擴縮；LangGraph 管單一 Agent 行程內的狀態機。判準見架構文件 §5 |
| **`agent_id`** | **`agent_version_id`** | 前者是長期識別；後者是實際執行的版本。**Run 記錄的是後者** |
| **`FAILED`** | **`CANCELLED`** | 前者是非預期失敗（含逾時、沙盒錯誤，用 `error.code` 區分）；後者是使用者主動取消 |

## 租戶與權限

| 詞 | 定義 |
|---|---|
| **Tenant** | 租戶，資料隔離的最外層邊界。**每個查詢都必須帶 `tenant_id`**，DB 端 RLS 兜底 |
| **API Key** | 外部系統呼叫用的長期憑證，綁定 tenant + scope |
| **短期 token** | 注入沙盒的憑證，TTL ≤ 60s，只能用來跟 Proxy 溝通 |
