# Dispatcher：主機資源與調度

> 一台主機的 CPU、記憶體、磁碟怎麼分給 Agent；上限是多少、滿了怎麼辦、什麼時候收回。
> 派工流程本身見 [dispatch.md](dispatch.md)；容器怎麼建見 [container-orchestration.md](container-orchestration.md)。

## 核心觀念：記憶體是唯一真正會爆的資源

每個 Agent 容器都是 **2 GB 記憶體、無 swap**。主機能同時開幾個容器，基本上就是「Docker 可用記憶體 ÷ 2 GB」。
所有上限都是為了**讓請求排隊，而不是讓主機被 OOM killer 清場** —— 排隊只是慢，OOM 是整台機器上的東西一起死。

## 每個容器的配額（寫死在 driver，不是設定項）

| 項目 | 值 | 超過時 |
|---|---|---|
| 記憶體 `mem_limit` | 2 GB，`memswap_limit` 同值（= 無 swap） | OOM killer 殺掉容器 |
| CPU `nano_cpus` | 2 core | 節流 |
| 行程數 `pids_limit` | 256 | fork 失敗（擋 fork bomb） |
| `/workspace` tmpfs | 512 MB，`noexec` | 寫入失敗 |
| 容器 log | json-file，10 MB × 1 檔 | 輪替 |

一次性沙盒、常駐容器、驗證容器都用同一組配額（`SandboxSpec` 的檢查不允許改）。

## 可調的上限（環境變數，`apps/api/config.py`）

| 變數 | 預設 | 管什麼 |
|---|---|---|
| `SANDBOX_MAX_CONCURRENT` | 10 | 同時存在的**一次性沙盒**數。滿了 → Run `FAILED` + `QUOTA_EXCEEDED`（retryable） |
| `SANDBOX_RUNS_PER_CONTAINER` | 50 | 一個常駐容器同時跑幾筆 Run（N）。多的在 Redis 排隊 |
| `RUN_QUEUE_TIMEOUT_SECONDS` | 3600 | Run 在佇列裡最多等多久；排隊時間**不算**進 Run 自己的逾時 |
| `CONTAINER_START_TIMEOUT_SECONDS` | 180 | 冷啟動（含 image pull）最多等多久，逾時退回一次性沙盒 |
| `CONTAINER_IDLE_TIMEOUT_SECONDS` | 1800 | 閒置多久回收。`0` = 不回收（**自動啟動之後不該設 0**） |
| `RUN_TIMEOUT_SECONDS` | 900 | Run 預設逾時；每筆 Run 的 `timeout_seconds` 硬上限也是 900 |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | 10 / 20 | `app` 的 asyncpg pool（min / max = 10 / 30） |

出口與寄信的次數、大小上限見 [gateway.md](gateway.md)。

### N 與記憶體是一組的

> **N = 50 表示 50 個 Run 共用一個容器的 2 GB，平均每 Run 約 40 MB。**

LLM agent 大部分時間在等 I/O，純等 LLM 回應的 Run 通常夠用。不夠的情況很具體：agent 把大 payload（長文件、大 JSON、圖片）
整份讀進記憶體，幾個並行就吃光額度，**同容器的 N 個一起被 OOM 帶走**。調 N 時要一起看記憶體，不能只調一個。

### 一個版本只開一個容器

**不做溢出擴容。** 一個版本就是一個常駐容器，滿了多的在 Redis 排隊，容器空出位子才會來拿 ——
容器在 long-poll 之前先取自己的並發名額（`ZENTRA_MAX_CONCURRENT_RUNS` = N 的 semaphore），沒位子就不取工作。
所以單一 Agent 的記憶體天花板固定是 **2 GB**，尖峰只會讓排隊變長，不會多吃記憶體。

例外：同一個 Agent 同時有兩個版本在被呼叫時，每個版本各一個容器。這只會短暫發生：

- **換版**：新版本發布並成為最新版時，worker 送 `zentra.container.drain_other_versions` ——
  其他版本的容器等手上的 Run 跑完就停（每 30 秒檢查，最多等一小時，之後交給閒置回收）。
  只停容器、不刪 image（舊版本仍可用版號指定）；被同版號取代的版本另由 `retire_version` 收 image。
- **呼叫端指定舊版號**：會再拉起舊版本的容器，閒置 30 分鐘後回收。

**主機規劃**（以 Docker 可用記憶體計）：

| 要同時服務 | 至少需要 |
|---|---|
| 平台本身（postgres、redis、registry、app、worker） | ~2 GB |
| 每個「正在被呼叫」的 Agent（的每個版本） | + 2 GB |
| 一次性沙盒併發 10 個（全部退回時的最壞情況） | + 20 GB |

macOS 用 Colima 時，VM 低於 6 GB 會把 Docker daemon 拖死。

## 調度：一筆 Run 去哪個容器

```
找「這個版本」的容器（容器綁定單一版本，不能跨版本）：
  READY 且 inflight < N           → inflight + 1（FOR UPDATE，同交易），推進佇列
  READY 但滿了                    → 推進佇列排隊，不記在容器頭上；**不開第二個容器**
  沒有容器（或還在 STARTING）       → 冷啟動並等它，最多 CONTAINER_START_TIMEOUT_SECONDS
  冷啟動失敗或逾時                 → 退回一次性沙盒（受 SANDBOX_MAX_CONCURRENT 限制）
```

- **防止同時開十個**：十個請求同時打進一個沒容器的 Agent，用 `pg_advisory_xact_lock(版本 id)` 讓「檢查 + 登記 STARTING」只有一個人做；
  鎖只罩住這一瞬間，不罩住 image pull。拿到鎖後要重查一次（等鎖期間別人可能已經開好了）。
- **排隊是 FIFO**（`LPUSH` + `BRPOP`），先到的 Run 先被容器拿走；取走時才把 inflight 記到容器上。
- **排隊不算進逾時**：排隊中的 Run 是 `SCHEDULING`，被容器取走才轉 `RUNNING`、記 `started_at`、發 run token。
  排隊本身的上限是 `RUN_QUEUE_TIMEOUT_SECONDS`（見 [dispatch.md](dispatch.md) §逾時）。

### 一次性沙盒的併發租約

Redis Sorted Set `sandbox:inflight`（member = run_id，score = 登記時間），Lua 原子地做三件事：剪掉超過 1200 秒的租約 → 檢查數量 < 上限 → 加入。
用**租約而不是計數器**：worker 崩潰時釋放不會被呼叫，計數器會永久殘留把上限吃光；租約 1200 秒（> Run 最長 900 秒）後自動剪掉。
常駐容器與驗證容器**不佔**這個名額；常駐容器的並發記在 `agent_containers.inflight_runs`。

## 回收

| 什麼 | 何時 | 怎麼判斷 |
|---|---|---|
| 閒置常駐容器 | beat 每 60 秒掃一次 | `READY`、`inflight_runs = 0`、**最後一次派工給它**（沒有就從啟動時間算）早於閒置門檻 |
| 一次性沙盒 | Run 結束（finally） | 一律銷毀，絕不重用 |
| 驗證容器 | 驗證結束（finally） | 一律銷毀 |
| 被取代版本的容器與 image | 綁著它的 Run 都結束後 | 見 [agent-registry.md](agent-registry.md) §舊版本的回收 |
| 被刪 Agent 的容器與 image | 刪除 commit 之後 | 同上 §刪除 Agent |

- **閒置不能看 `last_health_at`**：容器每 25 秒 long-poll 一次，那欄永遠是新的 —— 它量的是「活著」，不是「有事做」。
- 有 Run 在跑的容器（`inflight_runs > 0`）不管多久都不收。
- **自動產生的東西必須自動消失**：容器是被第一筆 Run 自動拉起來的，使用者沒按過啟動，也不會想到要按停止。

## 磁碟

| 誰在吃 | 上限 | 回收 |
|---|---|---|
| 內部 Registry（volume `registrydata`） | 主機磁碟。**無配額、無預檢、無自動 GC** | 刪 manifest 只是標記；真正釋放要停機跑 `registry garbage-collect` |
| Docker image cache | 主機磁碟 | 取代、刪 Agent 時刪平台用的 digest 參照 |
| PostgreSQL（`pgdata`） | 主機磁碟 | `run_events`、`runs`、`llm_calls`、`egress_calls` 只增不減，沒有保留期 |

- 磁碟滿了的表現是使用者的 `docker push` 在 client 端失敗，**平台不會顯示任何訊號**。已發布的版本不受影響。
- ⚠️ **`registry garbage-collect --delete-untagged` 絕對不准用**：同一個 tag 推第二次會讓前一個 manifest 變 untagged，而它可能正是某個 PUBLISHED 版本。
### 內部 Registry 的維運

- 主機端是 `127.0.0.1:6000`，**不是 5000**：macOS 的 AirPlay Receiver 佔著 `*:5000`（症狀是 `403` + `Server: AirTunes`）。容器內仍是 `registry:5000`。
  port 會變成已註冊 `image_ref` 的一部分存進 DB，**換 port 會讓那些版本拉不到**。
- 判斷 registry 活著只能看這個 header，不能只看連得上：`curl -sI http://127.0.0.1:6000/v2/ | grep -i docker-distribution-api-version`
- `REGISTRY_STORAGE_DELETE_ENABLED=true`（平台取代版本、刪 Agent 時要刪 manifest）。
- 未被任何版本引用的 repository，**平台不刪、不列、不管**（分不出是垃圾還是五分鐘後要註冊的東西）。手動回收：

```bash
curl -s http://localhost:6000/v2/_catalog | jq -r '.repositories[]'          # 1. registry 裡有什麼
docker compose exec -T postgres psql -U zentra -d zentra -At -c \
  "SELECT image_ref FROM agent_versions WHERE status IN ('PUBLISHED','BUILDING');"   # 2. 還在用的
# 3. 人工比對，只動兩邊都確認過的；用 DELETE /v2/<repo>/manifests/<digest> 刪 manifest。不要寫腳本自動刪
docker compose stop registry                                                    # 4. 停機
docker compose run --rm registry registry garbage-collect /etc/docker/registry/config.yml   # 5. 沒有 --delete-untagged
docker compose up -d registry                                                   # 6. 起回來
```

- 備份：`pgdata` 與 `registrydata` **必須一起備份**（DB 的 `image_ref` 指向 registry 的 manifest），`ZENTRA_SECRET_KEY` 另外保管。Redis 不用備份。

## 其他行程與連線

| 項目 | 值 |
|---|---|
| Celery worker | 1 個，`--concurrency 10`、`prefetch 1`。常駐路徑派完就返回，不等結果，所以 10 個行程不會變成瓶頸 |
| Postgres 連線 | app pool 最多 30 + worker 每子行程 1 條（10）+ migration 1；總和要低於 `max_connections`（預設 100） |
| 交易內閒置 | app / worker 連線設 `idle_in_transaction_session_timeout = 60s`，見 [postgresql.md](postgresql.md) |

## 目標（NFR）

| 項目 | 目標 |
|---|---|
| 冷啟動 | P95 < 2s（image 已在本機 cache 時；registry pull 屬發布階段） |
| API 額外延遲 | P95 < 100ms |
| Run 最長 | 15 分鐘 wall-clock |
| 可用性 | **不設目標**。單機即單點故障，主機重開 = 停機 |
