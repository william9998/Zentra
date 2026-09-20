# Feature Spec：ZEN-000 貫穿骨架 (Walking Skeleton)

| 欄位 | 值 |
|---|---|
| 狀態 | `SPEC_READY` |
| 擁有者 | @architect |
| 對齊 Spec | `specs/SPEC.md v0.2.1` |
| 涉及分層 | L0 · L1 · L2 · L3（全部四層） |
| 相關 ADR | [ADR-0001](../../adr/ADR-0001-sandbox-isolation.md)、[ADR-0002](../../adr/ADR-0002-celery-langgraph-boundary.md)、[ADR-0003](../../adr/ADR-0003-agent-packaging-dual-interface.md)、[ADR-0005](../../adr/ADR-0005-mvp-single-host-scope.md) |
| 排程 | 第 1–2 週 |

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
  → Celery task 進 q.run
  → runner-worker 取件 → SandboxDriver.create()
  → 沙盒啟動 → 平台 compile 一個寫死的 trivial LangGraph（2 節點）
  → 逐節點 emit 事件 → POST /internal/runs/{id}/events → Redis Streams
  → GET /v1/runs/{id}/events (SSE) 看得到事件
  → 結束 → runs 表 SUCCEEDED → 沙盒銷毀
```

具體交付物：
1. Monorepo 骨架 + `docker compose`（**postgres / redis / api / worker** —— 就是全部四個服務）
2. `zentra-sandbox-net`（`internal: true`）+ `api` 同時接上兩個網段
3. `runs` 與 `run_events` 資料表 + Alembic migration（含 `owner_id` 欄位，即使還沒強制執行）
4. `POST /v1/agents/{id}/runs`、`GET /v1/runs/{id}`、`GET /v1/runs/{id}/events`
5. Celery app（**Redis broker + result**）+ `q.run` + `runner-worker`（`acks_late=True` + 冪等）
6. `SandboxDriver` 介面 + `DockerSandboxDriver`（含 `capabilities()`）
7. 沙盒映像 `zentra/agent-runtime`：Python 3.12 + langgraph + 最小 `zentra_sdk`
8. Internal Gateway 的**最小兩條路由**：`GET /internal/runs/{id}/bootstrap`、`POST /internal/runs/{id}/events`
9. 寫死的 trivial agent：`echo_node → finalize_node`，**不呼叫任何 LLM**

### **明確不做**
- ❌ 認證授權（端點先開著，但 `owner_id` 欄位要存在）
- ❌ 上傳程式碼、版本發布（agent 先寫死在映像裡 → ZEN-001）
- ❌ `POST /internal/llm/*`（骨架不呼叫 LLM → ZEN-002）
- ❌ 完整的四層隔離硬化與自動化驗證（→ ZEN-003）
- ❌ cancel / 列表 / `Last-Event-ID` 續傳（→ ZEN-004）
- ❌ MCP 接口（→ ZEN-005）
- ❌ Console UI 與 `/console/*` 靜態檔路由（用 `curl` 驗證即可 → ZEN-006）
- ❌ 任何 LLM 呼叫 —— **刻意的**，才不會被非確定性干擾，專心驗證機制

## 3. 這條切片要證偽的四個假設

**這是本任務的真正價值，驗收要對著這四條看。**

| # | 假設 | 若為假會怎樣 |
|---|---|---|
| A1 | 沙盒可以在可接受時間內動態建立與銷毀，且不重用 | 冷啟超過 NFR 的 2s → 需要重新評估映像大小或啟動流程 |
| A2 | `runner-worker` 能在**完全不 import 使用者程式碼**的前提下完成派工與監看（P1） | P1 站不住 → 整個安全模型要重想 |
| A3 | 平台可以注入 compile 參數（事件 hook），使用者只交 `StateGraph`（ADR-0003） | 觀測無法強制 → ADR-0003 要改 |
| A4 | 事件能從沙盒內經 Internal Gateway 即時串到 SSE，延遲可接受 | 串流架構要換（例如沙盒改直連 Redis，但那會破壞 L2 不直連 L3 的硬規則） |

## 4. 對核心立場的影響

| 立場 | 觸碰 | 說明 |
|---|---|---|
| P1 使用者程式碼不在平台行程執行 | ✅ **核心驗證項** | worker 全程不得 import 沙盒內的 agent 模組 |
| P2 框架統一 LangChain/LangGraph | ✅ | trivial agent 就是一個 `StateGraph` |
| P3 API + MCP 雙門面 | ➖ | 本任務只做 REST，但 `entrypoints` 結構要先預留 |
| P4 沙盒無長期憑證 | ✅ | 沙盒走 `internal` 網段 + run token。**不得**為了方便把任何 secret 注入沙盒，也不得讓沙盒直連 Redis／Postgres |
| P5 版本不可變 | ➖ | agent 寫死在映像裡，尚未涉及版本管理 |

## 5. 契約變更

- [x] 無新增變更。實作 `specs/api-contract.md v0.2.0` 既有端點的子集：
  `POST /v1/agents/{id}/runs`、`GET /v1/runs/{id}`、`GET /v1/runs/{id}/events`，
  以及 §4 Internal Gateway 的 `bootstrap` 與 `events` 兩條
- 回應欄位、狀態字串、錯誤 code **必須完全照契約**，不得因為「骨架先簡化」而偷改
- 狀態機**不得**出現 `WAITING_INPUT`（v0.2.0 已移除）

## 6. 分工

| 角色 | 工作 | 可寫範圍 |
|---|---|---|
| @platform-eng | monorepo 骨架、`docker compose`、沙盒網段、`SandboxDriver` + `DockerSandboxDriver`、沙盒映像 | `apps/sandbox/**`、`docker/**`、`compose.yaml` |
| @backend-dev | `runs` / `run_events` 資料表、三個公開端點、Internal Gateway 兩條路由、Celery app + runner-worker、SSE、最小 `zentra_sdk` | `apps/api/**`、`apps/worker/**`、`packages/sdk/**` |
| @qa-verifier | **先**寫 `acceptance.md`，再驗證 | `tests/**`、本目錄 `acceptance.md` |

前端不參與。Console 留到 ZEN-006（屆時是原生 HTML/CSS/JS，由 `api` 服務）。

## 7. 驗收條件

由 @qa-verifier 寫進 `specs/features/ZEN-000/acceptance.md`。**必須涵蓋**：

- 四個假設 A1–A4 各自有對應的可執行斷言
- **P1 的機器驗證**：`grep` worker 程式碼確認無 `import`/`exec` 使用者模組的路徑
- **冪等驗證**：同一個 `run_id` 的 Celery task 執行兩次，結果與副作用相同
- **沙盒不重用驗證**：跑兩個 Run，確認是兩個不同的容器 ID，且第一個已銷毀
- **L1 網路的最小驗證**：沙盒內連 `postgres:5432`、`redis:6379`、`1.1.1.1` **必須全部失敗**；
  連 `api:8000/internal` **必須成功**
- **契約一致性**：三個端點的回應逐欄位對照 `api-contract.md v0.2.0`
- **`capabilities()` 誠實性**：Docker driver 必須回報 `l2_kernel_isolation: false`，
  安全測試對應項標記 `skipped` 而非 `passed`

## 8. 風險與未決問題

| 風險 | 影響 | 對策 |
|---|---|---|
| Docker API 動態建容器的延遲超出 NFR 的 2s | A1 為假 | 先量測。共用 runtime image 已在本機，不需拉取，理論上夠快 |
| `internal: true` 網段下沙盒打不到 `api` | A4 卡關 | `api` 必須**同時**接上 `zentra-net` 與 `zentra-sandbox-net`。這是整條路徑的關鍵設定，先單獨驗證 |
| 開發時 `api` 跑在主機上（熱重載），沙盒連不到 | 開發受阻 | 見 [07-local-dev-sandbox.md](../../../docs/architecture/07-local-dev-sandbox.md) 的 `host.docker.internal` 說明，**僅限 `ZENTRA_ENV=dev`** |
| `docker.sock` 掛載的權限問題 | 開發受阻 | 只有 `worker` 掛。風險已在 ADR-0005 與架構文件 §9 記錄為常駐風險 |

**未決**：目前無。有疑問開 `SPEC-QUESTION` 於 `coordination/BOARD.md`。

## 9. 完成的定義

一句話：**`curl` 打一個 Run，能在 SSE 看到 `node.start` → `node.end` → `run.status: SUCCEEDED`，
`docker ps -a` 能看到那個沙盒容器已被建立並銷毀，且從沙盒內 `curl postgres:5432` 是不通的。**
