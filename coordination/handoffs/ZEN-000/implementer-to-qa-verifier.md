# 交接單 Handoff

**任務**：`ZEN-000` 貫穿骨架
**從**：主 session（實作）　**給**：@qa-verifier
**日期**：2026-09-21
**Spec 版本**：`v0.2.2`；API 契約 `v0.2.2`

## 1. 我做了什麼

在單機 docker compose（4 服務）上把 ZEN-000 的完整路徑打通並實測：
`POST /v1/agents/{id}/runs` → Celery `q.run` → `DockerSandboxDriver` 建沙盒
→ 平台 compile trivial LangGraph → 逐節點事件經 Internal Gateway → Redis Stream → SSE
→ `SUCCEEDED` → 沙盒銷毀。全程約 700ms。

驗收閘門 `scripts/verify-zen000.sh`：**27 通過／0 失敗／2 skipped**。

## 2. 產出的檔案

| 檔案 | 說明 |
|---|---|
| `compose.yaml`、`docker/*.Dockerfile` | 4 服務 + 沙盒映像；`zentra-sandbox-net` 宣告為 `internal: true` |
| `apps/api/**` | REST 三端點、Internal Gateway 三路由、SSE、錯誤格式 |
| `apps/api/migrations/versions/0002_*`、`0003_*` | 補齊 models 已有但 migration 缺的欄位與約束 |
| `apps/worker/**` | Celery app、runner-worker、同步事件寫入 |
| `apps/sandbox/**` | `SandboxDriver` + `DockerSandboxDriver` + `SandboxManager` + 沙盒 runtime |
| `packages/sdk/zentra_sdk/**` | `ZentraContext`、`@agent`、`AgentLogger` |
| `scripts/verify-zen000.sh` | 驗收閘門，任何人可重跑 |
| `specs/features/ZEN-000/acceptance.md` | 驗收結果與未覆蓋範圍 |

## 3. 契約影響

- [x] 沒有變更對外契約。實作的是 `api-contract.md v0.2.2` 既有端點的子集。
- 實作語意與 SPEC v0.2.2 的澄清一致：**worker 唯一決定終態**（沙盒說自己成功不算數）、
  事件以 `(run_id, event_id)` 冪等落庫、SSE 以 DB `seq` 去重。
- **一項刻意偏離**：ZEN-000 §2 寫「Internal Gateway 最小兩條路由」，
  實際做了三條（多 `POST /internal/runs/{id}/result`）。沒有它沙盒無法回寫 output，
  §9「完成的定義」要求的 `SUCCEEDED` 就不可能達成。該路由本來就在 `api-contract.md` §4。
  **請裁定這是可接受的偏離，還是要回頭改 feature spec。**

## 4. 下一個人要做什麼

1. 獨立重跑 `bash scripts/verify-zen000.sh`，核對 27/0/2。
2. 讀 `acceptance.md` §5「沒有覆蓋到的」，判定是否足以放行 `DONE`。
3. 裁定 §3 的路由偏離。

## 5. 我沒做的事（誠實列出）

- **獨立 QA 沒有發生** —— 實作與驗證是同一個人做的。
- L3 資源限制只驗參數有套用，**沒跑 fork bomb／記憶體炸彈**（→ ZEN-003）。
- **併發上限（`QUOTA_EXCEEDED`）、逾時（`SANDBOX_TIMEOUT`）、worker 跑到一半被殺掉** 三條路徑未驗。
- 容器逃逸沒測，**本架構也不防**（ADR-0005 的已知取捨）。
- 沒寫 pytest 測試（本機無 uv／pytest，閘門改用純 curl + docker 的 shell 腳本）。

## 6. 已知限制與風險

| 項目 | 影響 | 後續方式 |
|---|---|---|
| migration 曾嚴重落後 models | 一跑就 `UndefinedColumn` | 建議 ZEN-004 加 CI：`alembic check` 必須無 diff |
| `gateway.send_events` 原本靜默吞錯 | 事件流空白卻無線索，查了很久 | 已改成印到 stderr；**觀測路徑的錯誤不准靜默** |
| ZEN-001 的 bind mount | worker 經 docker.sock 建容器時，volume 路徑是**主機路徑**不是容器路徑 | ZEN-001 要傳主機端的 `./data` 絕對路徑進 worker |
| 事件寫入有 async／sync 兩份實作 | 單邊修改會漂移 | 已在 `apps/worker/events.py` 標註，ZEN-004 收斂 |
| **Run 會卡在非終局狀態** | 事件寫入失敗時，worker 的 except 區塊**也會**寫事件而再次失敗，Run 就永遠停在 `SCHEDULING`／`RUNNING`，沒有任何機制會把它收掉（實測留下一筆 `01a0bfc3`） | ZEN-004 應加 **stuck run reaper**：定期把超過 timeout 仍非終局的 Run 標成 `FAILED`；並讓終態寫入不依賴事件寫入成功 |

## 7. 未決的 SPEC-QUESTION

- [x] 無新增。§3 的路由偏離請 @qa-verifier 或 @architect 裁定。
