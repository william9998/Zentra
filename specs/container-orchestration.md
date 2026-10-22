# 容器怎麼建、怎麼隔離

> Agent 的程式碼在哪裡跑、容器怎麼建、隔離到什麼程度、常駐容器的一生。
> 資源配額與上限看 [dispatcher-resources.md](dispatcher-resources.md)；Run 的派工看 [dispatch.md](dispatch.md)。

## 三種容器

| 模式 | 用途 | 壽命 | 一個容器服務 |
|---|---|---|---|
| `SERVE`（常駐容器） | **預設的執行路徑** | 第一筆 Run 拉起，閒置後回收 | 一個版本、最多 N 筆並行的 Run |
| `RUN`（一次性沙盒） | 常駐容器起不來時的退路 | 一筆 Run，結束就刪，**絕不重用** | 一筆 Run |
| `VALIDATE`（驗證容器） | 發布版本時，import 使用者模組、抽出 entrypoints | 驗證完就刪 | 一個版本 |

三種用**同一個 image**（使用者的 Agent image）、同一組隔離與配額。差別只在啟動指令（`zentra-agent serve|run|validate`）和環境變數。

例外：內建測試 Agent `builtin-echo` 不是 OCI 版本，跑 `zentra/agent-runtime:dev`（BOARD ZEN-010 要退休它）。

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
                              └─起不來，或一啟動就退出──▶ FAILED ──重啟──▶ STARTING（restart_count + 1）
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

### 停止、重啟、回收

| 動作 | 做什麼 |
|---|---|
| 停止 | 撤銷**這一個容器**的 token → 銷毀 → `STOPPED`、`inflight_runs = 0` |
| 重啟 | 同上，再走一次啟動；`restart_count + 1`（一直重啟的容器是壞掉了，這個數字讓它看得見） |
| 閒置回收 | 排程每 60 秒掃一次，看 [dispatcher-resources.md](dispatcher-resources.md) §回收 |
| 版本被取代 | `zentra.version.retire`：等綁著舊版本的 Run 都結束，再停容器並刪舊 image（[agent-registry.md](agent-registry.md)） |
| 新版本成為最新版 | `zentra.container.drain_other_versions`：其他版本的容器等手上的 Run 跑完就停（不刪 image） |
| 刪 Agent | `zentra.agent.purge` 逐一停掉 |

撤銷一律**以容器為單位**。同一個 Agent 可能同時有新舊兩個版本的容器；如果照 Agent 撤，會把另一個容器的 token 也撤掉，它還在長輪詢卻每次都 401，**畫面上看起來完全正常**。

### 容器只綁一個版本

一個常駐容器只服務一個 `agent_version_id`。發新版或取代版本後，新 Run 走新版本的容器；舊容器跑完手上的工作後被收掉——切換期間兩者並存。
所以工作佇列是「一個版本一條」（看 [redis.md](redis.md)）。

## 已知缺口

- **沒有存活監控。** `UNHEALTHY` 是保留值，**目前沒有任何路徑會寫入它**。
  常駐容器啟動後如果死掉（OOM、程式崩潰），資料庫仍然顯示 `READY`，新工作照樣被派進它的佇列卻沒人取，
  直到逾時清掃把它收成 `SANDBOX_TIMEOUT`，或閒置回收把它標成 STOPPED。
- 常駐容器裡卡死的 Run（例如在 `async def` 裡做阻塞呼叫）不會被殺，只是帳面上被逾時清掃收掉。
- ZEN008-ISO-01（`VOLUME` 逃逸），見上。
