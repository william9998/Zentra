# 派工系統設計

> 一筆 Run 從 API 進來，到容器裡跑完、終態寫回資料庫的整條路。
> 容器怎麼建見 [container-orchestration.md](container-orchestration.md)；上限與資源見 [dispatcher-resources.md](dispatcher-resources.md)。

## 原則

1. **worker 只派工與監看，不執行使用者程式碼**（P1）。它只碰 Docker API、DB、Redis。
2. **終態只有 worker 能決定。** 沙盒回寫的結果只被保存，不會讓 Run 轉狀態 —— 否則沙盒就能宣告自己成功。
3. **每次狀態轉移與它的 `run.status` 事件在同一個 DB 交易寫入**，所以事件流與表上的狀態不會不一致。
4. **先 commit 到 Postgres，再發 Redis。** Redis 只是通知，不是事實來源。

## Celery

- broker 與 result backend 都是 Redis。worker 跑 `-Q q.run,q.build --concurrency 10 -B`（embedded beat，所以**只能有一個 worker**）。
- `task_acks_late=True` + `task_reject_on_worker_lost=True`：worker 跑到一半死掉，任務回到佇列重派。
- `worker_prefetch_multiplier=1`：長任務不預取。
- 同一件事用固定 task id 送（`run_id`、`build:{version_id}`、`finalize:{run_id}`、`container:{action}:{cid}`）。

| 任務 | 佇列 | 做什麼 |
|---|---|---|
| `zentra.run.execute` | `q.run` | 派一筆 Run（本文件主體） |
| `zentra.run.finalize` | `q.run` | 常駐容器回寫結果後，裁決終態 |
| `zentra.build.execute` | `q.build` | 版本驗證與發布（[agent-registry.md](agent-registry.md)） |
| `zentra.container.apply` | `q.build` | 容器 start / restart / stop |
| `zentra.container.sweep_idle` | `q.build` | beat 每 60 秒，回收閒置容器 |
| `zentra.agent.purge` | `q.build` | 刪 Agent 後清容器與 image |
| `zentra.version.retire` | `q.build` | 版本被取代後清舊容器與舊 image |

慢的、會拉 image 的都走 `q.build`，不要塞進 `q.run` 卡住 Run 派工。

## Run 狀態機（唯一定義）

```
QUEUED → SCHEDULING → RUNNING → ┬→ SUCCEEDED
                                ├→ FAILED      （逾時 / 容器錯誤 / 使用者程式錯誤，以 error.code 區分）
                                └→ CANCELLED   （cancel 端點尚未實作，見 api.md）
```

前端、後端、測試都只用這六個字串（`apps/api/enums.py::RunStatus`）。**`WAITING_INPUT` 已移除，不要寫也不要斷言。**

## 主流程

```
POST /v1/agents/{id}/runs
  app：驗 input schema → 解析版本（指定版號的現役那筆，或最新 PUBLISHED）→ INSERT runs (QUEUED) → 送 zentra.run.execute
  回 202 { run_id }

worker：zentra.run.execute
  1. 鎖 Run 列；不是 QUEUED 就放棄（重複投遞的冪等）→ 轉 SCHEDULING
  2. 預留一次性沙盒名額（Redis 租約，滿了 → FAILED + QUOTA_EXCEEDED）
  3. 發 run token（TTL = timeout_seconds）
  4. 讀版本：狀態必須是 PUBLISHED 或 SUPERSEDED（建立時綁的就是那一筆，取代不影響它）
  5. 順手掃逾時的常駐 Run（見 §逾時）
  6. 選路 ─┬─ 常駐容器（預設）
           └─ 一次性沙盒（退路）
```

### 路徑 A：常駐容器（預設）

```
6a. 同一個交易：找這個「版本」READY 且 inflight < N 的容器（FOR UPDATE），找到就 inflight + 1
6b. 找不到時：
     · 有 READY 但滿了 → 只排進 Redis 佇列，不記在容器頭上，**不開第二個容器**；容器空出位子才來拿
     · 一個都沒有     → 冷啟動並**等它**（上限 CONTAINER_START_TIMEOUT_SECONDS）；
                        起不來 → 退回路徑 B，Run 照樣跑
6c. **狀態維持 SCHEDULING、不記 started_at**（有預留名額的記 container_id）→ commit
6d. **commit 之後**才 LPUSH 工作項到 agent_work:{agent_id}:{version_id}
6e. 撤銷第 3 步發的 run token（常駐路徑用不到它）、放掉一次性沙盒名額，任務返回 —— 不等結果
```

工作項帶：`run_id`、`entrypoint`、`input`、`manifest`、`default_model`、`config`、`secret_names`。

```
容器 ──GET /internal/agents/{id}/work（long-poll 25s）──▶ app BRPOP 該版本的佇列
          （容器先取自己的並發名額，沒位子就不來取）        ├ Run 已終結（排隊太久被收掉）→ 丟掉，繼續等下一筆
                                                         ├ Run 還在 SCHEDULING → 同一個交易：轉 RUNNING、記 started_at、
                                                         │   歸屬到取走它的容器（inflight）→ commit → **發 run token**
                                                         │   （TTL = timeout_seconds，從這一刻算）→ 200 + 工作項（含 run_token）
                                                         └ 沒有   → 204，容器立刻再 poll
容器跑完 ──POST /internal/runs/{id}/result──▶ app 只保存 output/error/usage → 送 zentra.run.finalize
worker：zentra.run.finalize
  鎖 Run → 依保存的 error 決定 SUCCEEDED / FAILED（同交易寫終局事件）→ 撤銷 run token → 該容器 inflight - 1
```

- **一個版本一條佇列**，不是一個 Agent 一條：容器綁定單一版本，共用佇列會讓舊容器撈走新版本的工作（實測踩過）。
- **排隊不算進逾時**：排隊中的 Run 是 `SCHEDULING`（畫面「配置中」），被容器取走才轉 `RUNNING` 開始計時。
  不維持 `QUEUED` 是因為 `QUEUED → SCHEDULING` 是 worker 防止重複投遞的認領動作，退回去會讓重送的任務再派一次。
- **排隊有上限**：等超過 `RUN_QUEUE_TIMEOUT_SECONDS`（預設 1 小時）還沒被取走 → `FAILED` + `SANDBOX_TIMEOUT`（訊息寫明是排隊太久）。
  容器死掉時佇列裡的工作永遠不會被取走，沒有這條上限它們會永遠卡著。
- **先 commit 再 LPUSH**：反過來的話容器可能在 DB 寫完前就取走工作，`inflight_runs` 被加兩次（實測踩過）。
  反向風險是「commit 了但沒推出去」，那筆 Run 會停在 SCHEDULING 直到排隊上限收掉 —— 慢，但帳是對的。
- **歸屬校正**：排隊的工作派工時沒有記在容器上，取走時才記到真正取走它的容器（`attribute_run`）。
- **`inflight_runs` 永遠不超過 N**：全滿時進來的工作不預留給任何容器。

### 路徑 B：一次性沙盒（退路）

```
6x. 建容器（env 只有 ZENTRA_GATEWAY_URL / ZENTRA_RUN_ID / ZENTRA_RUN_TOKEN）→ 轉 RUNNING（記 sandbox_id）
6y. driver.wait(timeout = timeout_seconds)
6z. 依結束狀態裁決終態（同交易寫終局事件）
finally：撤銷 token → 銷毀容器（冪等）→ 放掉名額   ← 順序有意義
```

沙盒內：`GET /internal/runs/{id}/bootstrap` 取 manifest、input、config… → 跑 graph → `POST /result` → 退出。

### 終態裁決（一次性沙盒）

| 條件 | 終態 | error.code |
|---|---|---|
| 逾時 | FAILED | `SANDBOX_TIMEOUT` |
| 沙盒已回寫 error | FAILED | 沙盒回報的（通常 `USER_CODE_ERROR`） |
| exit code ≠ 0 | FAILED | `SANDBOX_ERROR`（帶 log 尾 500 字） |
| exit 0 但沒回寫結果 | FAILED | `SANDBOX_ERROR` —— **沒回報不能推定成功** |
| 其餘 | SUCCEEDED | — |

worker 自己出例外：Docker 平台錯誤 → `SANDBOX_ERROR`；其他 → `INTERNAL_ERROR`。最後一道網保證 Run 不會卡在 RUNNING。

## 事件

```
沙盒 ──POST /internal/runs/{id}/events [ {event_id, type, payload}, … ]──▶ app
  INSERT run_events（(run_id, event_id) 唯一 → 重送不會寫兩列）→ commit → XADD run:{run_id} → 寫 published_at
```

- `event_id` 由沙盒產生（UUID v7），**重送沿用原值**；SSE 的 `id` 是 DB 的 `seq`。
- DB commit 成功但 `XADD` 失敗 → 回暫時失敗，沙盒用同一個 `event_id` 重試，查回原 `seq`。
- 沙盒可送 `node.start` · `node.end` · `token` · `log` · `custom` · `error`；**`run.status` 只由平台產生**。
- `run_events` 對 `(run_id) WHERE terminal` 有部分唯一索引：每筆 Run 至多一個終局事件，worker 重試寫不出第二個。
- `publish_pending`：每次轉狀態後補送還沒 `XADD` 的事件。

SSE 的讀法：從 `run_events` 依 `seq` 讀（去重與排序靠 DB），`XREAD BLOCK 1000` 只用來被叫醒；Redis 出錯就 sleep 1 秒繼續。
Redis 掉通知、重送、整個 key 過期，client 看到的事件都不會少也不會重複，最多慢一秒。

## 逾時

| 路徑 | 誰抓 | 怎麼收 |
|---|---|---|
| 一次性沙盒 | `driver.wait(timeout)` | 容器被殺 → `SANDBOX_TIMEOUT` |
| 常駐容器（執行中） | `_sweep_timeouts`，**每次派工時順手掃**（最多 50 筆） | `started_at`（被容器取走的時間）`+ timeout_seconds` 過了 → FAILED + `SANDBOX_TIMEOUT`，撤銷 token，inflight - 1 |
| 常駐容器（排隊中） | 同上 | `created_at + RUN_QUEUE_TIMEOUT_SECONDS` 過了還在 SCHEDULING → FAILED + `SANDBOX_TIMEOUT` |
| 同步呼叫 `runs:invoke` / MCP | API 端輪詢 60 秒 | 回 `SANDBOX_TIMEOUT`，**Run 本身繼續跑** |

⚠️ 常駐路徑的逾時清掃**沒有流量就不會跑**：最後幾筆卡住的 Run 會停在 RUNNING 直到下一次派工。
常駐容器裡的執行緒也不會被殺 —— 清掃只讓帳面誠實。

## 已知的缺口

- `POST /v1/runs/{id}/cancel` 未實作。
- Redis 遺失 `q.run` / `q.build` 時：排隊中的 Run 停在 `QUEUED`、`BUILDING` 的版本卡住，**沒有自動恢復**（逾時清掃只處理 RUNNING）。
- ZEN-000 的內建 fixture `builtin-echo`（`01930000-…-0001`）走非 OCI 的特例路徑（`zentra/agent-runtime:dev`），不走常駐容器。退休它是 BOARD ZEN-010。
