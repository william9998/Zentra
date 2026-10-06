# Redis 資料儲存規格

| | |
|---|---|
| 對齊程式碼 | `feat/resident-agent-container`（ZEN-015 之後） |
| 對齊 Spec | `specs/SPEC.md v0.3.1`、ADR-0005（broker 與 result 都用 Redis，不用 RabbitMQ）、ADR-0012（常駐容器） |
| 服務 | compose 的 `redis`（`redis:7-alpine`，實測 7.4），只掛 `zentra-net`，**db 0** |
| 最後核對 | 2026-10-01，以程式碼中所有 key 的定義與執行中 Redis 的 `--scan` 對照 |

這份文件列出平台在 Redis 裡用到的**每一種 key**：格式、資料型別、TTL、誰寫、誰讀、何時刪。
PostgreSQL 那一半見 [`PostgreSQL.md`](./PostgreSQL.md)。

> **新增任何 Redis key 都要回來補這份文件。** key 格式一律定義在一個函式裡（`key_for()`、`queue_key()` …），
> 不要在呼叫端手拼字串。

---

## 1. 定位：Redis 只放會過期的東西

| | PostgreSQL | Redis |
|---|---|---|
| 放什麼 | 事後要查得到的紀錄 | 佇列、短命憑證、即時通知、併發計數 |
| 整個清空會怎樣 | 平台失去歷史 | **進行中的工作會掉**（排隊中的 Run、正在跑的 Run 的 token），**歷史一筆都不會掉** |
| 是不是事實來源 | ✅ | ❌（唯一例外是憑證的有效性，見 §3.3） |

**判斷新資料該放哪的規則：** 如果它不見了，有沒有人事後會問「那筆去哪了」？會 → PostgreSQL。

### 1.1 誰能連 Redis

| 服務 | 能連 | 用途 |
|---|---|---|
| `app` | ✅ | 發／驗 token、寫事件通知、SSE 讀通知、常駐容器取工作（BRPOP） |
| `worker` | ✅ | Celery broker 與 result backend、發 token、派工到常駐容器佇列、併發計數 |
| 沙盒（一次性與常駐） | ❌ | **沙盒網段沒有到 `redis` 的路由**（P4）。常駐容器取工作是打 `app` 的 `GET /internal/agents/{id}/work`，由 `app` 代為 BRPOP |

---

## 2. Key 總表

| Key 格式 | 型別 | TTL | 用途 | 定義於 |
|---|---|---|---|---|
| `run:{run_id}` | Stream | 3600 秒 | Run 事件的即時通知 | `apps/api/services/events.py` `stream_key()` |
| `run_token:{token}` | String | = Run 的 `timeout_seconds` | run token → run_id | `apps/api/services/runtoken.py` |
| `run_tokens:{run_id}` | Set | = Run 的 `timeout_seconds` | 一個 Run 的所有有效 token（撤銷用索引） | 同上 |
| `validation_token:{token}` | String | ≤ 600 秒 | 驗證 token → agent_version 的 id | `apps/api/services/validationtoken.py` |
| `validation_tokens:{version_id}` | Set | ≤ 600 秒 | 一個版本驗證的所有 token | 同上 |
| `container_token:{token}` | String | 30 天 | container token → `{agent_id}:{container_uuid}` | `apps/api/services/containertoken.py` |
| `container_tokens:{agent_id}` | Set | 30 天 | 一個 Agent 的所有 container token | 同上 |
| `container_tokens_of:{container_uuid}` | Set | 30 天 | 一個容器的 container token | 同上 |
| `agent_work:{agent_id}:{agent_version_id}` | List | 無 | 常駐容器的工作佇列（一個**版本**一條） | `apps/api/services/work.py` `queue_key()` |
| `sandbox:inflight` | Sorted Set | 2400 秒（每次寫入續期） | 一次性沙盒的併發租約 | `apps/sandbox/manager.py` |
| `q.run`、`q.build` | List | 無 | Celery 任務佇列 | `apps/worker/celery_app.py` |
| `unacked`、`unacked_index` | Hash / Sorted Set | 無 | Celery 已取出未 ack 的任務（`acks_late`） | kombu 內部 |
| `_kombu.binding.*` | Set | 無 | kombu 的路由綁定 | kombu 內部 |
| `celery-task-meta-{task_id}` | String | 86400 秒 | Celery 任務結果 | Celery result backend |

`{token}` 都是 `secrets.token_urlsafe(32)`（43 字元、256 位元隨機）。`{run_id}` 等 id 都是 UUID 字串。

---

## 3. 各 key 詳細

### 3.1 `run:{run_id}` —— 事件通知（Stream）

**只是「有新事件了」的鈴聲，不是事件本身。** 事件的事實來源是 PostgreSQL 的 `run_events` 表。

| | |
|---|---|
| 型別 | Stream |
| 每筆 entry 的欄位 | `seq`（對應 `run_events.seq`）、`type`、`data`（payload 的 JSON 字串） |
| 寫入 | `app` 的 Gateway 收到沙盒事件時、`worker` 轉 Run 狀態時：**先 commit 到 `run_events`，再 `XADD`**，成功後回寫 `run_events.published_at` |
| 讀取 | SSE 端點（`apps/api/services/streaming.py`）以 `XREAD BLOCK 1000` 等通知 |
| TTL | 每次 `XADD` 後 `EXPIRE 3600` |
| 刪除 | 靠 TTL；Run 結束一小時後自然消失 |

**SSE 的讀法（為什麼它不是事實來源）：**

```
loop:
  1. SELECT run_events WHERE run_id=? AND seq > last_seq ORDER BY seq LIMIT 200   ← 從 DB 讀、用 seq 排序與去重
  2. 送出這些事件；讀到 terminal=true 就結束
  3. XREAD run:{run_id} BLOCK 1000                                                ← 只用來「等下一次叫醒」
  4. Redis 出錯 → sleep 1 秒繼續，不中斷串流
```

所以 Redis 掉了一則通知、重送了一則、甚至整個 key 過期了，client 看到的事件都不會少也不會重複 ——
最多慢一秒（下一輪 poll）。

### 3.2 `run_token:*` / `run_tokens:*` —— Run token（P4 的實作）

**沙盒唯一拿得到的憑證。** 只能對 Internal Gateway 使用，綁定單一 Run。

| Key | 型別 | 值 |
|---|---|---|
| `run_token:{token}` | String | 綁定的 `run_id` |
| `run_tokens:{run_id}` | Set | 這個 Run 目前所有有效的 `{token}` |

| | |
|---|---|
| 發放 | `worker` 開始執行 Run 時（`runtoken.issue_sync`）。一次性沙盒放進 env `ZENTRA_RUN_TOKEN`；常駐容器則**跟著工作項一起進去**（見 §3.5） |
| 驗證 | `app` 的 `/internal/*`：`GET run_token:{token}` 取得 run_id，再比對路徑上的 run_id，**帶 A 的 token 存取 B 會被擋** |
| TTL | = 該 Run 的 `timeout_seconds`（最長 900 秒） |
| 撤銷 | Run 進入終態時 `revoke_run_sync`：讀 `run_tokens:{run_id}`，把裡面每個 `run_token:*` 刪掉。之後同一個 token 一律 401 |

> **為什麼存 Redis 不存資料表**：TTL 與撤銷是 Redis 的原生能力；而且 token 是 Run 期間才存在的東西，
> 沒有人事後要查。Redis 清空時正在跑的 Run 會因為 token 失效而失敗 —— 這是可接受的退化。

### 3.3 `validation_token:*` / `validation_tokens:*` —— 版本驗證 token

發布版本時，worker 在一次性沙盒裡跑 `zentra-agent validate`，沙盒用這把 token 把抽出的 entrypoints
回寫給 Gateway。

| Key | 型別 | 值 |
|---|---|---|
| `validation_token:{token}` | String | agent_version 的 `id` |
| `validation_tokens:{version_id}` | Set | 這次驗證的所有 token |

| | |
|---|---|
| 發放 | `worker` 的 `q.build` 任務（`validationtoken.issue_sync`），TTL 固定夾在 1～600 秒，實際給 600 |
| 撤銷 | 驗證結束時 `revoke_version_sync` |
| 權限 | 只能回寫**那一個版本**的驗證結果；版本已離開 `BUILDING` 就一律拒絕 |

### 3.4 `container_token:*` 等 —— 常駐容器的取工作憑證

常駐容器啟動時還不知道要跑哪個 Run，它得先「問」，這一問需要憑證。

| Key | 型別 | 值 |
|---|---|---|
| `container_token:{token}` | String | `{agent_id}:{container_uuid}` |
| `container_tokens:{agent_id}` | Set | 該 Agent 所有容器的 token。`revoke_agent*` 用它一次撤銷，但**目前沒有呼叫端**（見下方） |
| `container_tokens_of:{container_uuid}` | Set | **單一容器**的 token（停一個容器時只撤銷它自己的） |

| | |
|---|---|
| 發放 | `worker` 建立常駐容器時，TTL 30 天 |
| 能做的事 | **只有一件**：`GET /internal/agents/{綁定的 agent}/work` 取工作。不能讀寫 Run、不能呼叫 LLM、不能取別的 Agent 的工作 |
| 撤銷 | 一律以容器為單位（`revoke_container_sync`）：停止、重啟、失敗、閒置回收時撤銷該容器的；刪除 Agent 時逐一撤銷它每個容器的。TTL 只是忘記撤銷時的保險 |

> **它按字面是「長期憑證」，為什麼不違反 P4**：靠的不是短命，而是**權限窄到洩漏也沒用** ——
> 最壞情況是某 Agent 的容器搶到自己 Agent 的工作，而那本來就是它的工作。
> 真正有威力的 run token 仍然是一個 Run 一把、跑完就沒（`containertoken.py` 的模組說明）。
>
> **`container_tokens:{agent_id}` 目前只寫不讀。** `revoke_agent` / `revoke_agent_sync` 存在但沒有呼叫端 ——
> 多容器之後它們會連兄弟容器一起撤掉，所以所有撤銷都改走 `container_tokens_of`。這個 Set 仍在發放時寫入，靠 TTL 消失。
>
> **為什麼要分 `container_tokens_of`**：一個 Agent 可以有多個容器。如果照 Agent 撤銷，
> 停掉一個容器會把兄弟容器的 token 一起撤掉 —— 它們還在 long-poll，卻每次都拿到 401，畫面上看起來完全正常。

### 3.5 `agent_work:{agent_id}:{agent_version_id}` —— 常駐容器工作佇列

```
worker（派工）  ──LPUSH──▶  agent_work:{agent_id}:{version_id}  ◀──BRPOP──  app（代容器取）
                                                                           ▲
                                        容器 ──GET /internal/agents/{id}/work（long-poll 25 秒）──┘
```

| | |
|---|---|
| 型別 | List，`LPUSH` + `BRPOP` = **FIFO**（先到的 Run 先處理） |
| 每個項目 | JSON：`run_id`、**`run_token`**、`entrypoint`、`input`、`manifest`、`default_model` |
| 寫入 | `worker` 的 `q.run` 任務，**在 commit Run 的 `container_id` 與 `inflight_runs` 之後**才 LPUSH |
| 讀取 | `app` 收到容器的 long-poll 時 `BRPOP`，最多等 25 秒；沒工作回 204，容器重新 poll |
| TTL | 無 |
| 刪除 | 該版本最後一個容器停止時 `DEL`（沒人會來取了，留著只會讓那些 Run 停在 RUNNING 直到逾時） |

> **一個版本一條佇列，不是一個 Agent 一條。** 容器綁定單一版本（P5）。共用一條的話，切換版本期間
> 新舊容器並存，舊容器會把新版本的工作撈走：說好跑 1.0.2 的 Run 實際跑了 1.0.0（實測踩過）。
>
> **先 commit 再 LPUSH 的理由**：反過來的話，容器可能在 DB 寫完之前就取走工作，
> `inflight_runs` 會被加兩次（實測踩過）。反向風險是「commit 了但沒推出去」，那筆 Run 會停在 RUNNING
> 直到逾時清掃收掉 —— 慢，但帳是對的。
>
> ⚠️ **項目裡有 run token 與完整 input。** 能讀 Redis 的人就能在 Run 期間冒用那個 Run 的身分。
> 這是 Redis 必須只掛 `zentra-net`、不可接沙盒網段的原因之一。

### 3.6 `sandbox:inflight` —— 一次性沙盒併發上限

| | |
|---|---|
| 型別 | Sorted Set：member = run_id，score = 登記時的 Unix 時間。只有一次性沙盒的 Run 會預留（`apps/worker/tasks/run.py`）；版本驗證沙盒與常駐容器不佔這個名額 |
| 上限 | `SANDBOX_MAX_CONCURRENT`（預設 10，SPEC §10） |
| 預留 | Lua script 原子地做三件事：剪掉超過 1200 秒的租約 → 檢查 `ZCARD < 上限` → `ZADD`。超過上限回 `QUOTA_EXCEEDED` |
| 釋放 | 沙盒結束時 `ZREM` |
| TTL | 每次預留時 `EXPIRE 2400`（租約 1200 秒 × 2） |

> **為什麼是租約而不是計數器**：worker 崩潰時 `ZREM` 不會被呼叫。計數器會永久殘留、把上限慢慢吃光；
> 租約則在 1200 秒（大於 Run 最長的 900 秒）後被下一次預留自動剪掉。
>
> 常駐容器的並發不走這個 key，而是 PostgreSQL 的 `agent_containers.inflight_runs`。

### 3.7 Celery 使用的 key

平台不直接讀寫這些，但它們佔 Redis、出問題時要看得懂。

| Key | 說明 |
|---|---|
| `q.run` | List。Run 派工任務（`zentra.run.execute`、`finalize`）。排隊中的任務在這裡 |
| `q.build` | List。版本發布（`zentra.build.execute`）、容器生命週期（`zentra.container.apply`）、每 60 秒的閒置容器回收。**容器操作走這條**，因為它跟發布一樣要拉 image、會慢，不能卡住 Run 派工 |
| `unacked` / `unacked_index` | 已被 worker 取出、尚未 ack 的任務。設定了 `task_acks_late=True`：worker 在執行中死掉，任務會回到佇列 |
| `_kombu.binding.q.run` 等 | kombu 的 exchange 綁定，啟動時建立 |
| `celery-task-meta-{task_id}` | 任務結果，Celery 預設保留 1 天（實測剩餘 TTL 與此一致）。**平台不讀這些結果**：Run 的結果在 PostgreSQL |

> ⚠️ **kombu 的 Redis transport 有 visibility timeout（預設 3600 秒）**：任務取出後超過這個時間沒 ack，
> 會被重新投遞。Run 最長 900 秒，所以目前不會觸發；若哪天任務會跑超過一小時，要先調這個值。
>
> 平台以 `task_id=f"build:{version_id}"`、`f"finalize:{run_id}"` 等固定 id 送任務，所以同一件事重送時
> `celery-task-meta-*` 只會有一筆。

---

## 4. 持久化與容量

| 項目 | 現況 | 影響 |
|---|---|---|
| AOF | 關閉（`--appendonly no`） | |
| RDB | Redis 預設規則（`save 3600 1 300 100 60 10000`），寫在容器的 `/data` | |
| `/data` 的儲存 | **匿名 volume**，compose 沒有給它具名 volume | 容器重啟、`docker compose up` 重建時保留；**`docker compose down` 後再 up 會拿到一個新的空 volume** |
| `maxmemory-policy` | `noeviction` | 記憶體滿時寫入會失敗，而不是悄悄丟掉 key。對佇列與 token 來說這是對的 —— 丟掉會讓 Run 憑空消失 |
| 實測用量 | 約 1.7 MB（2026-10-01，閒置時） | 主要是 `celery-task-meta-*` |

**Redis 資料遺失時會發生什麼**（照 §1 的原則，這些都是可接受的退化）：

| 遺失的東西 | 後果 | 恢復 |
|---|---|---|
| `q.run` / `q.build` 裡的任務 | 排隊中的 Run 停在 `QUEUED`；`BUILDING` 的版本卡住 | **目前沒有自動恢復**：逾時清掃只處理 `RUNNING`。這些 Run 會一直停在 `QUEUED`；版本要用新的 semver 重新註冊 |
| `agent_work:*` | 已派進常駐容器佇列的 Run 停在 `RUNNING` | 逾時清掃收成 FAILED。清掃是「每次派工時順手掃」，**沒有新流量就不會掃** |
| `run_token:*` | 正在跑的 Run 回寫結果時 401 → 失敗 | 重新呼叫 |
| `container_token:*` | 常駐容器取不到工作 | 重啟容器（Console「重啟」或等閒置回收後重新拉起） |
| `run:*` Stream | **不影響**。SSE 從 `run_events` 讀 | — |
| `sandbox:inflight` | 併發計數歸零，短時間可能超過上限 | 自動恢復 |

**不需要備份 Redis。** 備份範圍是 `pgdata` 與 `registrydata`（見 [`PostgreSQL.md`](./PostgreSQL.md) §5）。

---

## 5. 除錯常用指令

在容器內跑（主機上的 6379 也開著，但從主機連容易連錯實例）：

```bash
docker compose exec redis redis-cli

# 依前綴看有哪些 key（不要用 KEYS *，會擋住整個 Redis）
SCAN 0 MATCH agent_work:* COUNT 100

# 某版本的常駐容器佇列還排了幾筆
LLEN agent_work:<agent_id>:<agent_version_id>

# 某 Run 的事件通知
XRANGE run:<run_id> - +

# 目前佔著幾個一次性沙盒名額
ZCARD sandbox:inflight

# Celery 佇列長度
LLEN q.run
LLEN q.build
```

---

## 6. 變更紀錄

| 版本 | 日期 | 變更 |
|---|---|---|
| v1.0 | 2026-10-01 | 初版：run 事件 Stream、三種 token、常駐容器工作佇列、沙盒併發租約、Celery |
