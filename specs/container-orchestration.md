# 容器怎麼建、怎麼隔離

> Agent 的程式碼在哪裡跑、容器怎麼建、隔離到什麼程度、常駐容器的一生。
> 資源配額與上限看 [dispatcher-resources.md](dispatcher-resources.md)；Run 的派工看 [dispatch.md](dispatch.md)。

## 三種容器

| 模式 | 用途 | 壽命 | 一個容器服務 |
|---|---|---|---|
| `SERVE`（常駐容器） | **預設的執行路徑** | 第一筆 Run 拉起，閒置後回收 | 一個版本、最多 N 筆並行的 Run |
| `RUN`（一次性沙盒） | 常駐容器起不來時的退路（被存活檢查放棄的版本不退，見 §存活檢查） | 一筆 Run，結束就刪，**絕不重用** | 一筆 Run |
| `VALIDATE`（驗證容器） | 發布版本時，import 使用者模組、抽出 entrypoints | 驗證完就刪 | 一個版本 |

三種用**同一個 image**（使用者的 Agent image）、同一組隔離與配額。差別只在啟動指令（`zentra-agent serve|run|validate`）和環境變數。
沒有例外：平台沒有自己的 Agent 映像，冒煙測試與壓測用的 `probe-agent`（`examples/probe-agent`）也是一般的使用者 Agent。

## SandboxDriver：唯一碰 Docker 的介面

`apps/sandbox/drivers/base.py` 定義介面，目前只有 `DockerSandboxDriver`。
**worker 不直接呼叫 Docker，一律透過 driver**（`prepare_image` / `inspect_image` / `create` / `wait` / `destroy` / `remove_image` / `capabilities`）。這是以後要換成 K8s 時的逃生門。

- `destroy()` **必須冪等**（重複呼叫沒關係）：逾時、取消、崩潰、正常結束、重啟、回收，每種情況都會呼叫它。
- `capabilities()` **必須誠實**：`l2_kernel_isolation: false`（Docker 不是 gVisor）、`l5_sdk_interception: false`（那是 SDK 的事）。
- 只有 `worker` 掛 `docker.sock`。`app` 永遠不掛。

### 環境變數是「精確集合」（P4 的第一道關）

`_validate_environment()` 對每種模式檢查：**多一個、少一個都擋下來。**

| 模式 | env 恰好是這幾個 |
|---|---|
| `RUN` | `ZENTRA_GATEWAY_URL` · `ZENTRA_RUN_ID` · `ZENTRA_RUN_TOKEN` |
| `SERVE` | `ZENTRA_GATEWAY_URL` · `ZENTRA_AGENT_ID` · `ZENTRA_CONTAINER_TOKEN` · `ZENTRA_MAX_CONCURRENT_RUNS` |
| `VALIDATE` | `ZENTRA_GATEWAY_URL` · `ZENTRA_AGENT_VERSION_ID` · `ZENTRA_VALIDATION_TOKEN` |

**不准為了任何功能放寬這個檢查。** 設定、機密名字、模型預設值，都是每筆 Run 透過 Gateway（bootstrap 或工作項）帶進去的，不走 env。
LLM 金鑰、資料庫密碼、使用者的機密，**永遠不會出現在 env 裡**。

## 隔離：四層 Docker 隔離，沒有核心隔離

| 層 | 做了什麼 | 必須驗證成立 |
|---|---|---|
| **L1 網路** | 只接 `zentra-sandbox-net`（`internal: true`；建立時會檢查網段真的是 internal，否則拒絕）。唯一對端是 `app` | 連 `postgres:5432`、`redis:6379`、`1.1.1.1`、`169.254.169.254` **全部失敗**；連 `app:8000/internal/…` **成功** |
| **L2 核心** | `cap_drop: ALL`、`no-new-privileges`、`privileged: false`、user `65534:65534`、Docker 預設 seccomp。**沒有 gVisor** | `id` 不是 root；`unshare`、`ptrace`、`mount -t proc` 失敗 |
| **L3 資源** | 2 GB 無 swap、2 CPU、256 pids、wall-clock timeout | fork bomb、吃光記憶體、無窮迴圈都會被殺，而且不影響鄰居 |
| **L4 檔案** | `read_only` rootfs；只有 `/workspace` tmpfs 可寫（512 MB、`noexec`、`nosuid`）；沒有 volume、沒有 port、沒有 hostPath、沒有 docker.sock | 寫 `/`、`/app` 失敗；`/workspace` 可寫，而且隨容器消失 |

怎麼講才誠實：
- **缺的是核心隔離**：擋不住核心層級的容器逃逸。這個設計成立的前提是「只跑信任的程式碼」。描述時要說「四層 Docker 隔離，沒有核心隔離」，**不准說「沙盒很安全」**。
- 驗證時，driver 沒提供的那一層標成 `skipped`，**絕不標 `passed`**。
- **常駐容器裡，多筆 Run 共用同一個行程空間**：Run 之間沒有容器邊界，靠 SDK 把每筆 Run 的 token 綁在各自的 context 上。
  這等於把「容器邊界免費給的隔離」換成「靠程式碼寫對」，所以 Gateway 檢查「這個 token 屬於哪一筆 Run」就更重要。

⚠️ **已知隔離缺陷 ZEN008-ISO-01**：使用者 image 的 Dockerfile 如果宣告了 `VOLUME`，Docker 會建出一個**可寫**的匿名 volume，`read_only` 擋不住，而且容器刪掉後 volume 還留在主機上。尚未修（看 BOARD）。

### 網路拓撲

```
        zentra-net                          zentra-sandbox-net（internal）
  postgres  redis  registry  worker  ── app ──  Agent 容器（serve / run / validate）
                                       ▲
                                  唯一的橋
```

`postgres` / `redis` / `registry` / `worker` 絕不接 `zentra-sandbox-net`。容器的 image 是 Docker daemon 去拉的，容器自己不碰 Registry。

## 容器怎麼建

| 項目 | 值 |
|---|---|
| 名稱 | 常駐 `zentra-serve-{container_uuid}`；一次性 `zentra-run-{run_id}`；驗證 `zentra-validation-{version_id}` |
| label | `zentra.managed=true`、`zentra.role`、`zentra.resource_id`，再加 `zentra.agent_id` / `zentra.run_id` / `zentra.agent_version_id` |
| entrypoint | `/opt/zentra/bin/zentra-agent`，指令 = 模式 |
| 工作目錄 | `/workspace` |
| restart policy | 常駐容器是 `no`：重啟是**平台**決定的（因為要記 `restart_count`），不讓 Docker 自己重啟 |
| 拉 image | 用註冊時的精確 digest；建立前再 inspect 一次 |

## 常駐容器的一生

```
(第一筆 Run，或「預熱」) ──▶ STARTING ──確認活著──▶ READY ──停止／閒置回收／版本被取代──▶ STOPPED
                              │                      │
                              │                      └─存活檢查發現死了──▶ UNHEALTHY ──自動重啟──▶ STARTING
                              │                                              │
                              │                                   10 分鐘內第 4 次死掉
                              │                                              ▼
                              └─起不來，或一啟動就退出────────────────────▶ FAILED ──手動重啟──▶ STARTING
```

狀態存在 `agent_containers`（[postgresql.md](postgresql.md)）。`api` 只負責寫「想要的狀態」並發任務；真正的動作是 worker 的 `zentra.container.apply`，或派工時的冷啟動。

### 啟動（`start_container`）

**預熱（`POST /containers`）時，如果這個版本已經有容器在服務或啟動中 → 什麼都不做**，直接回那一列。
因為再派一次啟動會走下面第 1 步，把正在服務的容器連同跑到一半的 Run 一起砍掉重建。

1. 先銷毀殘骸：用資料庫存的 Docker id，**再加上固定的容器名**補一刀。
   （如果上次啟動在「建好容器」之後、「寫回資料庫」之前失敗，資料庫是空的，但 Docker 裡有一個同名容器，會撞名回 409。）
2. 發 container token（有效期 30 天，綁 `agent_id + container_uuid`）。
3. 拉精確 digest → 建 `SERVE` 容器。
4. **等 2 秒，再確認它還活著**：`READY` 表示「實際確認過還在跑」，不是「建立指令沒報錯」。
   一啟動就退出 → `FAILED`，`error` 帶容器自己的輸出（這是使用者唯一的線索）。
5. 失敗時，撤銷剛發的 token。

### 取工作：容器拉，平台不推

容器對 `GET /internal/agents/{agent_id}/work` 做長輪詢（25 秒）。

**選「拉」的原因是隔離，不是效能**：「推」要求容器監聽連線，會打破「容器不開 port、只有一個對外方向」這個性質——而那正是 L1 最容易驗證的特性。

- container token **只能**打這一條。打 `/internal/runs/*`、`/llm/*`、`/egress/*`、`/notify/*` 一律 `403`。
  它字面上是長期憑證，但權限窄到就算洩漏也沒用：最壞只是搶到自己那個 Agent 的工作。
- 每次取工作都會更新 `last_health_at`（`STARTING` / `UNHEALTHY` 會被翻成 `READY`）：**能來要工作，就是最好的健康訊號**，所以不另做 `/healthz`。
- 每個工作項帶的是**那一筆 Run 專屬**的 run token，由 app 在容器取走工作的那一刻才發；容器啟動時手上沒有任何 run token。
- 容器只在自己有空位時才長輪詢（`ZENTRA_MAX_CONCURRENT_RUNS` 的 semaphore），所以超過 N 的工作會留在 Redis 排隊。
- **交出工作前再驗一次 container token。** 容器死掉時，它最後那次長輪詢在 app 端還掛著（最多 25 秒），
  不擋的話會從佇列吞掉一筆工作、回給已經不存在的連線，那筆 Run 卡在 `RUNNING`。存活檢查發現它死了會先撤銷 token，
  所以 `BRPOP` 拿到工作後 token 已失效 → 把工作 `RPUSH` 放回佇列最前面、回 `204`。

### 停止、重啟、回收

| 動作 | 做什麼 |
|---|---|
| 停止 | 撤銷**這一個容器**的 token → 銷毀 → `STOPPED`、`inflight_runs = 0` |
| 重啟 | 同上，再走一次啟動；`restart_count + 1`（一直重啟的容器是壞掉了，這個數字讓它看得見） |
| 閒置回收 | 排程每 60 秒掃一次，看 [dispatcher-resources.md](dispatcher-resources.md) §回收 |
| 存活檢查 | 同一個 60 秒排程，看下面 §存活檢查 |
| 版本被取代 | `zentra.version.retire`：等綁著舊版本的 Run 都結束，再停容器並刪舊 image（[agent-registry.md](agent-registry.md)） |
| 新版本成為最新版 | `zentra.container.drain_other_versions`：其他版本的容器等手上的 Run 跑完就停（不刪 image） |
| 刪 Agent | `zentra.agent.purge` 逐一停掉 |

撤銷一律**以容器為單位**。同一個 Agent 可能同時有新舊兩個版本的容器；如果照 Agent 撤，會把另一個容器的 token 也撤掉，它還在長輪詢卻每次都 401，**畫面上看起來完全正常**。

### 存活檢查：容器死掉時

常駐容器的 Docker 重啟策略是 `no`，死掉就停在那裡，所以由平台自己發現、自己決定要不要重啟。
worker 的 `zentra.container.sweep_idle` 排程（每 60 秒）在回收閒置容器之後，接著做存活檢查：

1. **每個 `READY` 的容器都問 Docker**（`inspect`）。`Running = true` 就是活著；容器不存在、或 `Running = false` 才算死了。
   **不看心跳**：容器只在有空位時才長輪詢，全滿的容器心跳也會停，心跳停了分不出「在忙」還是「死了」。
   問 Docker 很便宜，直接問最多 60 秒就發現，不必先等心跳過期。
   **活著但卡死**也當成死了：Docker 說在跑，但 `last_health_at` 超過 **90 秒**沒更新（≈ 錯過 3 次長輪詢），
   而且 `inflight_runs < N`（有空位，照理一定會來要工作）。典型原因是在 `async def` 裡做阻塞呼叫，整個 event loop 停住。
   **先確認 app 健康**才下這個判斷：app 掛了或在重啟時，所有容器都要不到工作、心跳一起停，不確認會全部被誤殺。
2. **死了**（`UNHEALTHY`）：卡死的容器在這一步被銷毀（`docker rm -f`），之後跟死掉的走同一條路。
   - 死因寫進 `last_death_reason`、時間寫進 `last_died_at`：exit code、是不是 `OOMKilled`、容器最後的輸出。
     `error` 不放死因 —— 重啟成功時 `error` 會被清空，死因就不見了。
   - 撤銷這個容器的 container token、銷毀殘骸。先撤銷，`take_work` 才擋得住它留下的長輪詢（§取工作）。
   - **跑到一半的 Run**（`status = RUNNING` 且 `container_id` 是它）一律 `FAILED`，
     錯誤碼 `SANDBOX_ERROR`、`retryable: true`、訊息帶死因。**不自動重跑**：平台不知道 Agent 做了什麼副作用（例如寄信）。
     它們的 run token 立即撤銷。
   - **還在排隊的 Run** 不動：佇列是一個版本一條，重啟後的容器會接著取。
3. **重啟**：再走一次 §啟動，`restart_count + 1`。
   啟動流程把 `inflight_runs` **重算**成「還掛在這個容器上、沒結束的 Run 數」（手動重啟也一樣），不是歸零 —— 排隊的那些還掛在它身上。
4. **重啟上限：10 分鐘內 3 次。** `restart_window_started_at` 是這一輪的起點，`restarts_in_window` 是這一輪的次數；
   距離起點超過 10 分鐘就重新算。**第 4 次死掉就放棄這個版本**：
   - 容器 `FAILED`，`error` 寫明放棄的原因與最後一次死因。
   - **佇列裡的每一筆**（直接讀 `agent_work:{agent}:{版本}`，讀完即刪）`FAILED` + `AGENT_NOT_RUNNING`。
     讀佇列而不是查 DB：DB 分不出「已經推進佇列」和「還在派工途中」的 `SCHEDULING`。

處理期間（`UNHEALTHY`，通常幾秒）派工把它當成「正在啟動」**等它**，不另開新容器 —— 否則同一個版本會同時有兩個。

**放棄之後：明確停下來，等人處理。** 連續死掉通常是程式或資源（記憶體）真的有問題，退回一次性沙盒只會把問題藏起來。
「放棄」的判定：這個版本**最新的那一列**容器是 `FAILED` 且 `restarts_in_window >= 3`。

- **新的 Run**：`POST /v1/agents/{id}/runs`、`runs:invoke`、MCP `tools/call` 仍然**建立一筆 Run，但直接是 `FAILED` + `AGENT_NOT_RUNNING`**
  （訊息帶最後一次死因），不派工，並回 `409`（MCP 回 `isError`）。留紀錄是為了事後查「故障期間影響了誰」。
- **派工途中的 Run**（放棄前就建好、還沒派出去的）：worker 派工時再檢查一次，同樣 `FAILED` + `AGENT_NOT_RUNNING`。
- **告警**：平台只負責把狀態寫進 DB；發告警是監控主機的事（`monitoring/` 的 postgres-exporter 自訂查詢 + 告警規則）。
- **恢復**：手動重啟那個容器（`POST …/containers/{id}/restart`）會把 `restarts_in_window` 歸零；
  手動預熱（`POST …/containers`）會新增一列，判定就改看那一列。

冷啟動失敗（容器根本起不來）**不是**放棄：那條路仍然退回一次性沙盒，看 [dispatch.md](dispatch.md)。

偵測不到的情況，見 §已知缺口。

### 容器只綁一個版本

一個常駐容器只服務一個 `agent_version_id`。發新版或取代版本後，新 Run 走新版本的容器；舊容器跑完手上的工作後被收掉——切換期間兩者並存。
所以工作佇列是「一個版本一條」（看 [redis.md](redis.md)）。

## 已知缺口

- **滿載又卡死的容器要等一陣子才抓得到。** `inflight_runs >= N` 時不來要工作是正常的，分不出卡死；
  手上那些 Run 逾時被清掃收掉（inflight - 1）之後，有了空位卻還是不來，下一輪才會判定卡死。
- **發現有延遲**：最多 60 秒（排程間隔）。這段期間派進去的工作會在佇列等，重啟後被接走。
- **死掉到被發現之間被吞掉的工作擋不住**：那時 container token 還沒撤銷，`take_work` 的再驗一次會通過。
  機率低（長輪詢最多掛 25 秒），被吞的 Run 在下一次存活檢查時隨著「跑到一半的 Run」一起收成 `FAILED`。
- **放棄那一刻剛好在推佇列的工作**：派工端檢查完「沒放棄」、還沒推進佇列時發生放棄，那筆會留在已清空的佇列裡，
  由排隊上限（`RUN_QUEUE_TIMEOUT_SECONDS`）收掉。
- **單一 Run 卡住、但容器其他部分正常**（例如某個 Run 在等永遠不回應的外部呼叫）：容器照樣來要工作，不算卡死。
  那筆 Run 由逾時清掃收成 `SANDBOX_TIMEOUT`，但它的協程不會被中止，只是帳面上結束。
- ZEN008-ISO-01（`VOLUME` 逃逸），見上。
