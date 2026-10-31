# Run 派工

> 一筆 Run 從 API 進來，到在容器裡跑完、結果寫回資料庫，中間發生了什麼事。
> 容器怎麼建看 [container-orchestration.md](container-orchestration.md)；上限和資源看 [dispatcher-resources.md](dispatcher-resources.md)。

## 四條原則

1. **worker 只派工和監看，不執行使用者程式碼**（P1）。它只碰 Docker API、資料庫、Redis。
2. **只有 worker 能決定 Run 的最終結果。** 容器回報的結果只會被「存起來」，不會讓 Run 自己變成成功——否則容器就能宣告自己成功了。
3. **每次狀態改變，和對應的 `run.status` 事件，在同一個資料庫交易裡寫入。** 所以事件流和資料表上的狀態不會對不上。
4. **先寫進 Postgres（commit），再通知 Redis。** Redis 只是「叫醒你」，不是事實來源。

## 狀態機（唯一定義）

```
QUEUED → SCHEDULING → RUNNING → ┬→ SUCCEEDED
                                ├→ FAILED      （逾時／容器錯誤／使用者程式錯誤，用 error.code 區分）
                                └→ CANCELLED   （取消端點還沒實作，見 api.md）
```

| 狀態 | 意思 |
|---|---|
| `QUEUED` | 剛建好，等 worker 來認領 |
| `SCHEDULING` | worker 認領了，正在配置容器；**常駐容器全滿時，排隊等位子也是這個狀態**（畫面顯示「配置中」） |
| `RUNNING` | 容器真的開始跑了（`started_at` 就是這一刻） |
| `SUCCEEDED` / `FAILED` / `CANCELLED` | 終態 |

前端、後端、測試**只能用這六個字串**（`apps/api/enums.py::RunStatus`）。**`WAITING_INPUT` 已移除，不要寫、不要斷言。**

## 一個完整的例子

呼叫 `stock-agent`，容器已經開著、有空位：

```
t=0.0s   POST /v1/agents/{id}/runs
         app 驗證 input → 寫入 runs（QUEUED）→ 送 Celery 任務 → 回 202 {run_id}

t=0.1s   worker 認領：QUEUED → SCHEDULING
         找到一個 READY 且還有空位的容器，它的 inflight_runs 從 3 變 4
         commit 後，才把工作項推進 Redis 佇列 agent_work:{agent}:{version}

t=0.2s   容器長輪詢 GET /internal/agents/{id}/work，從佇列取到工作
         app：SCHEDULING → RUNNING、記 started_at、發 run token，連同工作內容回給容器

t=0.2~8s 容器跑 graph，過程中 POST /internal/runs/{id}/events 回報事件（SSE 就是從這來的）
         需要 LLM 或對外請求時，帶 run token 找 Gateway

t=8.4s   容器 POST /internal/runs/{id}/result，app 只把 output/error 存起來，然後送 finalize 任務
         worker finalize：看存起來的結果 → SUCCEEDED（或 FAILED）、撤銷 run token、inflight 4 → 3
```

下面把每一步展開。

## Celery 設定

- broker 和 result backend 都是 Redis。
- worker 啟動參數：`-Q q.run,q.build --concurrency 10 -B`。`-B` 是內嵌排程器（beat），所以**只能有一個 worker**，開兩個排程會重複跑。
- `task_acks_late=True` + `task_reject_on_worker_lost=True`：worker 跑到一半掛掉，任務會回到佇列重派。
- `worker_prefetch_multiplier=1`：長任務不預先搶。
- 同一件事用**固定的 task id** 送，避免重複：`run_id`、`build:{version_id}`、`finalize:{run_id}`、`container:{action}:{cid}`。

| 任務 | 佇列 | 做什麼 |
|---|---|---|
| `zentra.run.execute` | `q.run` | 派一筆 Run（本文件主體） |
| `zentra.run.finalize` | `q.run` | 常駐容器回報結果後，裁決最終狀態 |
| `zentra.build.execute` | `q.build` | 版本驗證與發布（[agent-registry.md](agent-registry.md)） |
| `zentra.container.apply` | `q.build` | 容器 start / restart / stop |
| `zentra.container.sweep_idle` | `q.build` | 排程每 60 秒跑一次，回收閒置容器 |
| `zentra.agent.purge` | `q.build` | 刪 Agent 後清容器與 image |
| `zentra.version.retire` | `q.build` | 版本被取代後清舊容器與舊 image |

慢的、要拉 image 的都放 `q.build`，不要塞進 `q.run` 擋住 Run 派工。

## 派工主流程（`zentra.run.execute`）

```
POST /v1/agents/{id}/runs
  app：驗 input schema → 決定版本（指定版號的現役那筆，或最新 PUBLISHED）
       → 這個版本的常駐容器被存活檢查放棄了？
           是 → INSERT runs（直接 FAILED + AGENT_NOT_RUNNING，不派工）→ 回 409（帶 run_id）
           否 → INSERT runs（QUEUED）→ 送 zentra.run.execute → 回 202 {run_id}

worker：zentra.run.execute
  1. 鎖住 Run 那一列；不是 QUEUED 就放棄（同一個任務被投遞兩次時的保護）→ 轉 SCHEDULING
  2. 預留一個一次性沙盒名額（Redis 租約；滿了 → FAILED + QUOTA_EXCEEDED）
  3. 發 run token（有效期 = timeout_seconds）
  4. 讀版本：狀態必須是 PUBLISHED 或 SUPERSEDED（Run 建立時綁定的就是這一筆，後來被取代也不影響）
  5. 這個版本被存活檢查放棄了 → FAILED + AGENT_NOT_RUNNING，結束（放棄前就建好、還沒派出去的 Run 走這裡）
  6. 選路：常駐容器（預設）／一次性沙盒（退路）
```

### 路徑 A：常駐容器（預設）

```
6a. 在同一個交易裡：找這個「版本」READY 且 inflight < N 的容器（FOR UPDATE 鎖住），找到就 inflight + 1
6b. 找不到時：
      · 有 READY 的但都滿了 → 只把工作放進 Redis 佇列，不算在任何容器頭上，
        也不會開第二個容器；等有容器空出位子來拿
      · 一個都沒有        → 冷啟動並「等它」（上限 CONTAINER_START_TIMEOUT_SECONDS）；
                           起來了 → 有條件地 inflight + 1（< N 才加；滿了就當排隊，不記 container_id）
                           起不來 → 退回路徑 B，Run 照樣會跑（被存活檢查放棄的版本除外：FAILED + AGENT_NOT_RUNNING）
6c. 狀態維持 SCHEDULING，不記 started_at（如果預留到位子，就記 container_id）→ commit
6d. commit 之後才把工作項 LPUSH 到 agent_work:{agent_id}:{version_id}
6e. 撤銷第 3 步發的 run token（常駐路徑用不到那把）、放掉一次性沙盒名額，任務結束——不等結果
```

工作項裡有：`run_id`、`entrypoint`、`input`、`manifest`、`default_model`、`config`、`secret_names`。

容器怎麼取走工作、怎麼回報結果：

```
容器 ──GET /internal/agents/{id}/work（長輪詢 25 秒）──▶ app 對該版本的佇列做 BRPOP
        （容器先確認自己有空位才來取）                    ├ Run 已經終結了（排隊太久被收掉）→ 丟掉，繼續等下一筆
                                                        ├ Run 還是 SCHEDULING → 同一個交易：
                                                        │    轉 RUNNING、記 started_at、
                                                        │    算在取走它的那個容器頭上（inflight）
                                                        │    → commit → 才發 run token（有效期從這一刻算）
                                                        │    → 回 200 + 工作項（含 run_token）
                                                        └ 沒有工作 → 回 204，容器馬上再問一次

容器跑完 ──POST /internal/runs/{id}/result──▶ app 只存 output/error/usage → 送 zentra.run.finalize

worker：zentra.run.finalize
  鎖住 Run → 依存起來的 error 決定 SUCCEEDED / FAILED（同一個交易寫終局事件）
  → 撤銷 run token → 該容器 inflight - 1
```

這裡的幾個設計為什麼是這樣：

- **一個「版本」一條佇列**，不是一個 Agent 一條。容器綁定單一版本，如果共用佇列，舊版容器會搶走新版的工作（實際踩過）。
- **排隊的時間不算進逾時。** 排隊中是 `SCHEDULING`；被容器取走轉 `RUNNING` 才開始計時。
  為什麼不乾脆維持 `QUEUED`？因為 `QUEUED → SCHEDULING` 是 worker 防止重複派工的「認領」動作，退回去的話，重送的任務會再派一次。
- **排隊有上限。** 等超過 `RUN_QUEUE_TIMEOUT_SECONDS`（預設 1 小時）還沒被取走 → `FAILED` + `SANDBOX_TIMEOUT`（訊息會寫明是排隊太久）。
  容器死掉時，佇列裡的工作永遠不會被取走；沒有這個上限它們會永遠卡著。
- **先 commit，再 LPUSH。** 反過來的話，容器可能在資料庫還沒寫完前就取走工作，造成 `inflight_runs` 被加兩次（實際踩過）。
  反方向的風險是「commit 了但沒推出去」：那筆 Run 會停在 SCHEDULING 直到排隊上限把它收掉——慢，但帳是對的。
- **取走時才校正歸屬。** 排隊的工作派工當下沒算在任何容器頭上，被取走時才記到真正取走它的容器（`attribute_run`）。
- **`inflight_runs` 永遠不會超過 N。** 全滿時進來的工作不預留給任何容器。

### 路徑 B：一次性沙盒（退路）

一筆 Run 一個容器，跑完就刪。

```
6x. 建容器（env 只有 ZENTRA_GATEWAY_URL / ZENTRA_RUN_ID / ZENTRA_RUN_TOKEN）→ 轉 RUNNING（記 sandbox_id）
6y. driver.wait(timeout = timeout_seconds)
6z. 依結束狀況裁決最終狀態（同一個交易寫終局事件）
finally：撤銷 token → 銷毀容器（重複銷毀沒關係）→ 放掉名額      ← 這個順序有意義
```

容器內部做的事：`GET /internal/runs/{id}/bootstrap` 取得 manifest、input、config…… → 跑 graph → `POST /result` → 結束。

#### 一次性沙盒怎麼判定結果

| 條件 | 最終狀態 | error.code |
|---|---|---|
| 逾時 | FAILED | `SANDBOX_TIMEOUT` |
| 容器已經回報 error | FAILED | 容器回報的那個（通常是 `USER_CODE_ERROR`） |
| exit code ≠ 0 | FAILED | `SANDBOX_ERROR`（附 log 最後 500 字） |
| exit 0 但沒回報結果 | FAILED | `SANDBOX_ERROR`——**沒回報不能當作成功** |
| 其他 | SUCCEEDED | — |

worker 自己出例外時：Docker 平台錯誤 → `SANDBOX_ERROR`；其他 → `INTERNAL_ERROR`。最後這道保險確保 Run 不會永遠卡在 RUNNING。

## 事件（SSE 的資料從哪來）

```
容器 ──POST /internal/runs/{id}/events [ {event_id, type, payload}, … ]──▶ app
  INSERT run_events（(run_id, event_id) 唯一 → 重送不會寫兩列）→ commit
  → XADD 到 Redis run:{run_id} → 回頭寫 published_at
```

- `event_id` 由容器產生（UUID v7），**重送時沿用原本的值**。SSE 的 `id` 則是資料庫的 `seq`。
- 資料庫寫成功但 `XADD` 失敗 → 回「暫時失敗」，容器用同一個 `event_id` 重試，平台會查回原本的 `seq`。
- 容器可以送：`node.start` · `node.end` · `token` · `log` · `custom` · `error`。**`run.status` 只有平台能產生。**
- `run_events` 有一個部分唯一索引 `(run_id) WHERE terminal`：每筆 Run 最多一個終局事件，worker 重試也寫不出第二個。
- `publish_pending`：每次轉狀態後，把還沒 `XADD` 的事件補送。

SSE 端怎麼讀：從 `run_events` 依 `seq` 讀（去重和排序靠資料庫）；`XREAD BLOCK 1000` 只用來「被叫醒」；Redis 出錯就睡 1 秒再試。
所以 Redis 漏了通知、重送、整個 key 過期，client 看到的事件都不會少、不會重複，最多慢一秒。

## 逾時

| 路徑 | 誰負責抓 | 怎麼收 |
|---|---|---|
| 一次性沙盒 | `driver.wait(timeout)` | 容器被殺 → `SANDBOX_TIMEOUT` |
| 常駐容器（執行中） | `_sweep_timeouts`，worker 的 **60 秒排程**（`zentra.container.sweep_idle`，最多 50 筆） | `started_at`（被取走的時間）`+ timeout_seconds` 已過 → FAILED + `SANDBOX_TIMEOUT`，撤銷 token，inflight - 1 |
| 常駐容器（排隊中） | 同上 | `created_at + RUN_QUEUE_TIMEOUT_SECONDS` 已過還在 SCHEDULING → FAILED + `SANDBOX_TIMEOUT` |
| 同步呼叫 `runs:invoke` / MCP | API 端輪詢 60 秒 | 回 `SANDBOX_TIMEOUT`，**Run 本身繼續跑** |

⚠️ 常駐容器裡的執行緒不會被殺——清掃只是讓帳面誠實，不是真的中止那段程式。

## 已知的缺口

- `POST /v1/runs/{id}/cancel` 沒實作。
- Redis 弄丟 `q.run` / `q.build` 的任務時：排隊中的 Run 會停在 `QUEUED`、`BUILDING` 的版本會卡住，**沒有自動恢復**（逾時清掃只處理 RUNNING）。
- ZEN-000 的內建測試 Agent `builtin-echo`（`01930000-…-0001`）走非 OCI 的特例路徑（`zentra/agent-runtime:dev`），不走常駐容器。要退休它是 BOARD ZEN-010。
