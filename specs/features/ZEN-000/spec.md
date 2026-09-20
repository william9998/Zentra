# Feature Spec：ZEN-000 貫穿骨架 (Walking Skeleton)

| 欄位 | 值 |
|---|---|
| 狀態 | `SPEC_READY` |
| 擁有者 | @architect |
| 對齊 Spec | `specs/SPEC.md v0.1.0` |
| 涉及分層 | L0 · L1 · L2 · L3 · L5 |
| 相關 ADR | [ADR-0001](../../adr/ADR-0001-sandbox-isolation.md)、[ADR-0002](../../adr/ADR-0002-celery-langgraph-boundary.md)、[ADR-0004](../../adr/ADR-0004-sandbox-driver-and-dev-environment.md) |

## 1. 要解決什麼問題

**這個任務不是要交付使用者價值，是要盡早證偽架構假設。**

Zentra 的技術風險集中在四個互相咬合的地方：沙盒生命週期、Celery 冪等、
LangGraph 由平台 compile 注入、事件串流。這四者**只有串起來跑才知道對不對**。
逐層建完再整合，等於把最大的風險推遲到最晚才發現。

因此第一個任務是一條**最薄的端到端切片**：一次 HTTP 呼叫，走完全部高風險元件，看到串流回來的事件。

## 2. 範圍

### 做
一條路徑，從頭到尾打通：

```
POST /v1/agents/{id}/runs
  → 寫 runs 表 (QUEUED) → 回 202 {run_id}
  → Celery task 進 q.agent.short
  → runner-worker 取件 → SandboxDriver.create()
  → 沙盒啟動 → 平台 compile 一個寫死的 trivial LangGraph（2 節點）
  → 逐節點 emit 事件 → Redis Streams
  → GET /v1/runs/{id}/events (SSE) 看得到事件
  → 結束 → runs 表 SUCCEEDED → 沙盒銷毀
```

具體交付物：
1. Monorepo 骨架 + `docker compose`（postgres / redis / rabbitmq）
2. `runs` 資料表 + Alembic migration（含 `tenant_id` 欄位，即使還沒強制執行）
3. `POST /v1/agents/{id}/runs`、`GET /v1/runs/{id}`、`GET /v1/runs/{id}/events`
4. Celery app + `q.agent.short` + `runner-worker`（`acks_late=True` + 冪等）
5. `SandboxDriver` 介面 + `DockerSandboxDriver`（含 `capabilities()`）
6. 沙盒映像：Python 3.12 + langgraph + 最小 `zentra_sdk`
7. 寫死的 trivial agent：`echo_node → finalize_node`，**不呼叫任何 LLM**
8. 事件經 Redis Streams 串到 SSE

### **明確不做**
- ❌ 認證授權（端點先開著，但 `tenant_id` 欄位要存在）
- ❌ 多租戶強制隔離（下一個任務做）
- ❌ Tool Proxy / LLM Proxy / Egress Proxy（骨架不打外網）
- ❌ 知識庫、Hybrid Search
- ❌ MCP 接口（ZEN-007 做）
- ❌ Console UI（用 `curl` 驗證即可）
- ❌ 上傳程式碼、Build pipeline（agent 先寫死在映像裡）
- ❌ gVisor（本機 Docker driver，見 ADR-0004）
- ❌ HITL / checkpoint 續跑
- ❌ 任何 LLM 呼叫 —— **刻意的**，才不會被非確定性干擾，專心驗證機制

## 3. 這條切片要證偽的四個假設

**這是本任務的真正價值，驗收要對著這四條看。**

| # | 假設 | 若為假會怎樣 |
|---|---|---|
| A1 | 沙盒可以在可接受時間內動態建立與銷毀，且不重用 | 冷啟太慢 → 需要 Warm Pool 提前做，或重新評估隔離方案 |
| A2 | `runner-worker` 能在**完全不 import 使用者程式碼**的前提下完成派工與監看（P1） | P1 站不住 → 整個安全模型要重想 |
| A3 | 平台可以注入 compile 參數（checkpointer / 事件 hook），使用者只交 `StateGraph`（ADR-0003） | 觀測與持久化無法強制 → ADR-0003 要改 |
| A4 | 事件能從沙盒內即時串到 SSE，延遲可接受 | 串流架構要換（例如改用 WebSocket 或直連） |

## 4. 對核心立場的影響

| 立場 | 觸碰 | 說明 |
|---|---|---|
| P1 使用者程式碼不在平台行程執行 | ✅ **核心驗證項** | worker 全程不得 import 沙盒內的 agent 模組 |
| P2 框架統一 LangChain/LangGraph | ✅ | trivial agent 就是一個 `StateGraph` |
| P3 API + MCP 雙門面 | ➖ | 本任務只做 REST，但 `entrypoints` 結構要先預留 |
| P4 沙盒無長期憑證 | ➖ | 骨架不打外網，但**不得**為了方便把任何 secret 注入沙盒 |
| P5 版本不可變 | ➖ | agent 寫死在映像裡，尚未涉及版本管理 |

## 5. 契約變更

- [x] 無新增變更。實作 `specs/api-contract.md v0.1.0` 既有的三個端點子集：
  `POST /v1/agents/{id}/runs`、`GET /v1/runs/{id}`、`GET /v1/runs/{id}/events`
- 回應欄位、狀態字串、錯誤 code **必須完全照契約**，不得因為「骨架先簡化」而偷改

## 6. 分工

| 角色 | 工作 | 可寫範圍 |
|---|---|---|
| @platform-eng | monorepo 骨架、docker compose、`SandboxDriver` + `DockerSandboxDriver`、沙盒映像 | `apps/sandbox/**`、`deploy/**`、`docker/**` |
| @backend-dev | `runs` 資料表、三個 API 端點、Celery app + runner-worker、SSE、最小 `zentra_sdk` | `apps/api/**`、`apps/worker/**`、`packages/sdk/**` |
| @qa-verifier | **先**寫 `acceptance.md`，再驗證 | `tests/**`、本目錄 `acceptance.md` |

前端不參與。Console 留到 ZEN-008。

## 7. 驗收條件

由 @qa-verifier 寫進 `specs/features/ZEN-000/acceptance.md`。**必須涵蓋**：

- 四個假設 A1–A4 各自有對應的可執行斷言
- **P1 的機器驗證**：`grep` worker 程式碼確認無 `import`/`exec` 使用者模組的路徑
- **冪等驗證**：同一個 `run_id` 的 Celery task 執行兩次，結果與副作用相同
- **沙盒不重用驗證**：跑兩個 Run，確認是兩個不同的容器 ID，且第一個已銷毀
- **契約一致性**：三個端點的回應逐欄位對照 `api-contract.md`
- **`capabilities()` 誠實性**：Docker driver 必須回報「無 L2」，安全測試對應項標記 `skipped` 而非 `passed`

## 8. 風險與未決問題

| 風險 | 影響 | 對策 |
|---|---|---|
| Docker API 動態建容器的延遲超出預期 | A1 為假 | 先量測再決定是否需要 Warm Pool |
| 沙盒內事件推到 Redis 的網路設定在 Colima 下不通 | A4 卡關 | 先用最簡單的 bridge 網路，L1 隔離留到 K8s driver |
| `docker.sock` 掛載在本機的權限問題 | 開發受阻 | 見 ADR-0004，本機可接受，正式環境改 K8s driver |
| arm64 / x86_64 套件相容性 | 上 CI 才爆 | Build 依賴解析以正式架構為準，CI 早期就跑 |

**未決**：目前無。有疑問開 `SPEC-QUESTION` 於 `coordination/BOARD.md`。

## 9. 完成的定義

一句話：**`curl` 打一個 Run，能在 SSE 看到 `node.start` → `node.end` → `run.status: SUCCEEDED`，
且 `docker ps -a` 能看到那個沙盒容器已被建立並銷毀。**
