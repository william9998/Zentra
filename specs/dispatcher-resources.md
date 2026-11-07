# 主機資源與調度

> 一台主機的 CPU、記憶體、磁碟怎麼分給 Agent；各種上限是多少、滿了怎麼辦、什麼時候回收。
> 派工流程看 [dispatch.md](dispatch.md)；容器怎麼建看 [container-orchestration.md](container-orchestration.md)。

## 最重要的觀念：記憶體是唯一真的會爆的資源

每個 Agent 容器固定 **2 GB 記憶體、不能用 swap**。
所以一台主機能同時開幾個容器，大致就是「Docker 可用記憶體 ÷ 2 GB」。

所有上限的目的都是：**寧可讓請求排隊，也不要讓主機被 OOM killer 清場。**
排隊只是慢；OOM 是整台機器上的東西一起死。

## 每個容器的配額

寫死在 driver，**不是設定項**，不能調。

| 項目 | 值 | 超過會怎樣 |
|---|---|---|
| 記憶體 `mem_limit` | 2 GB；`memswap_limit` 同值（= 不能用 swap） | OOM killer 殺掉容器 |
| CPU `nano_cpus` | 2 core | 被節流（變慢，不會被殺） |
| 行程數 `pids_limit` | 256 | fork 失敗（擋 fork bomb） |
| `/workspace` tmpfs | 512 MB，`noexec` | 寫入失敗 |
| 容器 log | json-file，10 MB × 1 檔 | 輪替（舊的被蓋掉） |

一次性沙盒、常駐容器、驗證容器都用同一組配額（`SandboxSpec` 的檢查不允許改）。

## 可以調的上限（環境變數，在 `apps/api/config.py`）

| 變數 | 預設 | 管什麼 |
|---|---|---|
| `SANDBOX_MAX_CONCURRENT` | 10 | 同時存在的**一次性沙盒**數量。滿了 → Run `FAILED` + `QUOTA_EXCEEDED`（可重試） |
| `SANDBOX_RUNS_PER_CONTAINER` | 50 | 一個常駐容器同時跑幾筆 Run（下面叫 **N**）。多的在 Redis 排隊 |
| `RUN_QUEUE_TIMEOUT_SECONDS` | 3600 | Run 在佇列裡最多等多久。排隊時間**不算**進 Run 自己的逾時 |
| `CONTAINER_START_TIMEOUT_SECONDS` | 180 | 冷啟動（含拉 image）最多等多久，逾時就退回一次性沙盒 |
| `CONTAINER_IDLE_TIMEOUT_SECONDS` | 1800 | 閒置多久回收。`0` = 永不回收（**容器會自動啟動，所以不該設 0**） |
| `RUN_TIMEOUT_SECONDS` | 900 | Run 預設逾時；每筆 Run 的 `timeout_seconds` 上限也是 900 |
| `APP_WORKERS` | 2 | `app` 開幾個 uvicorn 行程（compose.yaml 的 `--workers`）。一個行程最多吃滿一顆 CPU |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | 5 / 10 | `app` **每個行程**的 asyncpg 連線池（最少 5、最多 15 條）。總數 = `APP_WORKERS` × 15 |

對外請求與寄信的次數、大小上限看 [gateway.md](gateway.md)。

### N 和記憶體要一起看

> **N = 50 的意思是：50 筆 Run 共用一個容器的 2 GB，平均每筆約 40 MB。**

LLM Agent 大部分時間在等網路，純等 LLM 回應的 Run 通常夠用。會出問題的情況很具體：
Agent 把**大資料整份讀進記憶體**（長文件、大 JSON、圖片），幾筆並行就把額度吃光，
**同一個容器裡的 N 筆 Run 會一起被 OOM 帶走**。

所以調 N 的時候要同時考慮記憶體，不能只調一邊。

### 一個版本只開一個容器

**不做溢出擴容。** 一個版本就是一個常駐容器；滿了，多的 Run 在 Redis 排隊，等容器空出位子才被拿走。
做法：容器長輪詢之前，先拿自己的並發名額（`ZENTRA_MAX_CONCURRENT_RUNS` = N 的 semaphore），沒位子就不去取工作。

結果是：單一 Agent 的記憶體天花板固定是 **2 GB**。尖峰流量只會讓排隊變長，不會讓記憶體變多。

唯一的例外：同一個 Agent 同時有兩個版本在被呼叫時，**每個版本各一個容器**。這只會短暫發生：

- **換版**：新版本發布、成為最新版時，worker 送 `zentra.container.drain_other_versions`——其他版本的容器等手上的 Run 跑完就停（每 30 秒檢查，最多等一小時，之後交給閒置回收）。
  只停容器，不刪 image（舊版本還可以用版號指定）；被同版號取代的版本，image 另外由 `retire_version` 清。
- **呼叫端指定舊版號**：會再拉起舊版本的容器，閒置 30 分鐘後回收。

### 主機要準備多少記憶體

（以 Docker 可用記憶體計）

| 要同時服務 | 至少需要 |
|---|---|
| 平台本身（postgres、redis、registry、app、worker） | 約 2 GB |
| 每個「正在被呼叫」的 Agent（每個版本各算） | 加 2 GB |
| 一次性沙盒併發 10 個（全部退回一次性的最壞情況） | 加 20 GB |

**例子：** 同時有 3 個 Agent 在被呼叫 → 至少 2 + 3×2 = 8 GB。
macOS 用 Colima 時，VM 低於 6 GB 會把 Docker daemon 拖死。

## 調度：一筆 Run 去哪個容器

```
找「這個版本」的容器（容器只綁一個版本，不跨版本）：
  READY 而且 inflight < N        → inflight + 1（FOR UPDATE，同一個交易），工作推進佇列
  READY 但已滿                   → 推進佇列排隊，不算在任何容器頭上；不開第二個容器
  沒有容器（或還在 STARTING）      → 冷啟動並等它，最多 CONTAINER_START_TIMEOUT_SECONDS
  冷啟動失敗或逾時                → 退回一次性沙盒（受 SANDBOX_MAX_CONCURRENT 限制）
```

- **避免同時開十個容器**：十個請求同時打進一個還沒有容器的 Agent 時，用 `pg_advisory_xact_lock(版本 id)` 讓「檢查 + 登記 STARTING」只有一個人能做。
  鎖只罩住這一瞬間，不罩住拉 image 那段（那段很久）。拿到鎖之後要**重查一次**——等鎖的時候別人可能已經開好了。
- **排隊是先進先出**（`LPUSH` + `BRPOP`）。先到的 Run 先被容器取走；取走時才把 inflight 記到容器上。
- **排隊不算進逾時**：排隊中的 Run 是 `SCHEDULING`；被容器取走才轉 `RUNNING`、記 `started_at`、發 run token。
  排隊本身的上限是 `RUN_QUEUE_TIMEOUT_SECONDS`（看 [dispatch.md](dispatch.md) §逾時）。

### 一次性沙盒的併發名額（租約）

用 Redis Sorted Set `sandbox:inflight`（member = run_id，score = 登記時間）。一段 Lua 腳本原子地做三件事：
剪掉超過 1200 秒的舊租約 → 檢查數量是否 < 上限 → 加入。

**為什麼用「租約」不用「計數器」？** worker 崩潰時，「釋放」不會被呼叫。計數器會永遠殘留、慢慢把上限吃光；
租約在 1200 秒（比 Run 最長的 900 秒多）之後會自動被剪掉。

常駐容器和驗證容器**不佔**這個名額；常駐容器的並發記在 `agent_containers.inflight_runs`。

## 回收

| 什麼 | 何時 | 怎麼判斷 |
|---|---|---|
| 閒置的常駐容器 | 排程每 60 秒掃一次 | `READY`、`inflight_runs = 0`，而且「**最後一次派工給它**」（沒有就從啟動時間算）早於閒置門檻 |
| 一次性沙盒 | Run 結束時（finally） | 一律銷毀，絕不重用 |
| 驗證容器 | 驗證結束時（finally） | 一律銷毀 |
| 被取代版本的容器與 image | 綁著它的 Run 都結束後 | 看 [agent-registry.md](agent-registry.md) §舊版本的回收 |
| 被刪除 Agent 的容器與 image | 刪除 commit 之後 | 同上 §刪除 Agent |

- **判斷閒置不能看 `last_health_at`**：容器每 25 秒長輪詢一次，那個欄位永遠是新的。它量的是「還活著」，不是「有事做」。
- 有 Run 正在跑的容器（`inflight_runs > 0`）不管多久都不收。
- **自動產生的東西必須自動消失**：容器是被第一筆 Run 自動拉起來的，使用者沒按過「啟動」，也不會想到要按「停止」。

## 磁碟

| 誰在吃 | 上限 | 怎麼回收 |
|---|---|---|
| 內部 Registry（volume `registrydata`） | 主機磁碟。**沒有配額、沒有預檢、沒有自動 GC** | 刪 manifest 只是標記；真正釋放要停機跑 `registry garbage-collect` |
| Docker image cache | 主機磁碟 | 取代版本、刪 Agent 時，刪平台用的那個 digest 參照 |
| PostgreSQL（`pgdata`） | 主機磁碟 | `run_events`、`runs`、`llm_calls`、`egress_calls` **只增不減，沒有保留期限** |

- 磁碟滿了的症狀是：使用者的 `docker push` 在他自己那邊失敗。**平台不會顯示任何警告。** 已發布的版本不受影響。
- ⚠️ **`registry garbage-collect --delete-untagged` 絕對不准用。**
  原因：同一個 tag 推第二次，前一個 manifest 會變成 untagged，而它可能正好是某個 PUBLISHED 版本。

### 內部 Registry 的維運

- 主機端是 `127.0.0.1:6000`，**不是 5000**：macOS 的 AirPlay Receiver 佔著 `*:5000`（症狀是 `403` + `Server: AirTunes`）。容器內部仍是 `registry:5000`。
  port 會變成已註冊 `image_ref` 的一部分、存進資料庫，**所以換 port 會讓那些版本拉不到 image**。
- 判斷 registry 活著，只能看這個 header，不能只看連得上：

  ```bash
  curl -sI http://127.0.0.1:6000/v2/ | grep -i docker-distribution-api-version
  ```

- `REGISTRY_STORAGE_DELETE_ENABLED=true`（平台取代版本、刪 Agent 時要刪 manifest，所以要開）。
- 沒被任何版本引用的 repository，**平台不刪、不列、不管**（分不出那是垃圾，還是五分鐘後就要註冊的東西）。要手動回收：

```bash
# 1. registry 裡有什麼
curl -s http://localhost:6000/v2/_catalog | jq -r '.repositories[]'
# 2. 資料庫裡還在用的
docker compose exec -T postgres psql -U zentra -d zentra -At -c \
  "SELECT image_ref FROM agent_versions WHERE status IN ('PUBLISHED','BUILDING');"
# 3. 人工比對，只動「兩邊都確認過」的。用 DELETE /v2/<repo>/manifests/<digest> 刪 manifest。不要寫腳本自動刪
# 4. 停機
docker compose stop registry
# 5. GC（注意：沒有 --delete-untagged）
docker compose run --rm registry registry garbage-collect /etc/docker/registry/config.yml
# 6. 起回來
docker compose up -d registry
```

- **備份**：`pgdata` 和 `registrydata` **必須一起備份**（資料庫的 `image_ref` 指向 registry 的 manifest）；`ZENTRA_SECRET_KEY` 另外保管。Redis 不用備份。

## 其他行程與連線

| 項目 | 值 |
|---|---|
| Celery worker | 1 個，`--concurrency 10`、`prefetch 1`。常駐路徑派完就返回、不等結果，所以 10 個行程不會變成瓶頸 |
| app | `APP_WORKERS` 個 uvicorn 行程（預設 2）。app 是所有 HTTP 的入口：REST、MCP、Console、容器的 Gateway 呼叫（長輪詢、事件、LLM 代打）都在這裡。壓測 2026-11-01（每筆輸入 300 KB、4 核心主機）：1 個行程 CPU 卡在 100%、同步呼叫中位數 22 秒；2 個是 11 秒、吞吐量 1.8 倍；4 個和 2 個一樣快、多吃約 250 MB —— 再多就換 Postgres 卡住。主機核心多才調大 |
| Postgres 連線 | app `APP_WORKERS` × 15 條（預設 30）+ worker 每個子行程 1 條（共 10）+ migration 1 條。總和要低於 `max_connections`（預設 100）：`APP_WORKERS` 最多到 5 |
| 交易內閒置 | app / worker 連線設 `idle_in_transaction_session_timeout = 60s`，看 [postgresql.md](postgresql.md) |

## 目標（NFR）

| 項目 | 目標 |
|---|---|
| 冷啟動 | P95 < 2 秒（image 已在本機 cache；從 registry 拉屬於發布階段） |
| API 額外延遲 | P95 < 100ms |
| Run 最長 | 15 分鐘（wall-clock） |
| 可用性 | **不設目標**。單機就是單點故障，主機重開 = 停機 |
