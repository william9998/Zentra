# 容器化調度系統設計

> Agent 的程式碼在哪裡跑、容器怎麼建、隔離到什麼程度、常駐容器的生命週期。
> 資源配額與上限見 [dispatcher-resources.md](dispatcher-resources.md)；Run 的派工見 [dispatch.md](dispatch.md)。

## 三種容器

| 模式 | 用途 | 生命 | 一個容器服務 |
|---|---|---|---|
| `SERVE`（常駐容器） | **預設的執行路徑** | 第一筆 Run 拉起，閒置回收 | 一個版本、最多 N 筆並行 Run |
| `RUN`（一次性沙盒） | 常駐容器起不來時的退路 | 一筆 Run，結束即刪，絕不重用 | 一筆 Run |
| `VALIDATE`（驗證容器） | 發布版本時 import 使用者模組、抽 entrypoints | 驗證結束即刪 | 一個版本 |

三者用同一個 image（使用者的 Agent image）、同一組隔離與配額，差別只在 command（`zentra-agent serve|run|validate`）與 env。
內建 fixture `builtin-echo` 是例外：非 OCI，跑 `zentra/agent-runtime:dev`（BOARD ZEN-010 要退休它）。

## SandboxDriver：唯一碰 Docker 的介面

`apps/sandbox/drivers/base.py` 定義介面，MVP 只有 `DockerSandboxDriver`。**worker 不直接呼叫 Docker，一律透過 driver**
（`prepare_image` / `inspect_image` / `create` / `wait` / `destroy` / `remove_image` / `capabilities`），這是之後換 K8s 的逃生門。

- `destroy()` **必須冪等**：逾時、取消、崩潰、正常結束、重啟、回收都會呼叫它。
- `capabilities()` **必須誠實**：`l2_kernel_isolation: false`（Docker 不是 gVisor）、`l5_sdk_interception: false`（那是 SDK 的事）。
- 只有 `worker` 掛 `docker.sock`。`app` 永遠不掛。

### 環境變數是精確集合（P4 的第一道關）

`_validate_environment()` 對每個模式檢查「多一個少一個都擋」：

| 模式 | env 恰好是 |
|---|---|
| `RUN` | `ZENTRA_GATEWAY_URL` · `ZENTRA_RUN_ID` · `ZENTRA_RUN_TOKEN` |
| `SERVE` | `ZENTRA_GATEWAY_URL` · `ZENTRA_AGENT_ID` · `ZENTRA_CONTAINER_TOKEN` · `ZENTRA_MAX_CONCURRENT_RUNS` |
| `VALIDATE` | `ZENTRA_GATEWAY_URL` · `ZENTRA_AGENT_VERSION_ID` · `ZENTRA_VALIDATION_TOKEN` |

**不准為任何功能放寬這個檢查。** 設定、機密名字、模型預設值都經 Gateway（bootstrap 或工作項）每筆 Run 帶進去，不走 env。
LLM 金鑰、DB 密碼、使用者機密永遠不會出現在 env。

## 隔離（四層 Docker 隔離，無核心隔離）

| 層 | 措施 | 必須成立 |
|---|---|---|
| **L1 網路** | 只接 `zentra-sandbox-net`（`internal: true`，建立時檢查網段真的是 internal，否則拒絕）。唯一對端是 `app` | 連 `postgres:5432`、`redis:6379`、`1.1.1.1`、`169.254.169.254` 全失敗；連 `app:8000/internal/…` **成功** |
| **L2 核心** | `cap_drop: ALL`、`no-new-privileges`、`privileged: false`、user `65534:65534`、Docker 預設 seccomp。**無 gVisor** | `id` 非 root；`unshare`、`ptrace`、`mount -t proc` 失敗 |
| **L3 資源** | 2 GB 無 swap、2 CPU、256 pids、wall-clock timeout | fork bomb、記憶體炸彈、無窮迴圈被殺且不影響鄰居 |
| **L4 檔案** | `read_only` rootfs；只有 `/workspace` tmpfs 可寫（512 MB、`noexec`、`nosuid`）；無 volume、無 port、無 hostPath、無 docker.sock | 寫 `/`、`/app` 失敗；`/workspace` 可寫且隨容器消失 |

- **缺的是核心隔離**：擋不住核心層的容器逃逸。這成立的前提是「只跑信任的程式碼」。描述時要說「四層 Docker 隔離，無核心隔離」，**不准說「沙盒安全」**。
- 驗證時 driver 沒提供的那一層標 `skipped`，絕不標 `passed`。
- 常駐容器裡多筆 Run 共用一個行程空間：**Run 之間沒有容器邊界**，靠 SDK 把每筆 Run 的 token 綁在各自的 context 上。
  這是把容器邊界免費給的隔離改成「靠程式碼寫對」，所以 Gateway 的「token 屬於哪個 run」檢查更重要了。

⚠️ **已知隔離缺陷 ZEN008-ISO-01**：使用者 image 的 Dockerfile 若宣告 `VOLUME`，Docker 會建出可寫的匿名 volume，`read_only` 擋不住，
而且容器刪掉後 volume 留在主機上。尚未修（見 BOARD）。

### 網路拓撲

```
        zentra-net                          zentra-sandbox-net（internal）
  postgres  redis  registry  worker  ── app ──  Agent 容器（serve / run / validate）
                                       ▲
                                  唯一的橋
```

`postgres` / `redis` / `registry` / `worker` 絕不接 `zentra-sandbox-net`。沙盒的 image 由 daemon 拉，沙盒自己不碰 Registry。

## 容器的建立

| 項目 | 值 |
|---|---|
| 名稱 | 常駐 `zentra-serve-{container_uuid}`；一次性 `zentra-run-{run_id}`；驗證 `zentra-validation-{version_id}` |
| label | `zentra.managed=true`、`zentra.role`、`zentra.resource_id`，加 `zentra.agent_id` / `zentra.run_id` / `zentra.agent_version_id` |
| entrypoint | `/opt/zentra/bin/zentra-agent`，command = 模式 |
| 工作目錄 | `/workspace` |
| restart policy | 常駐容器 `no` —— 重啟是**平台**的決定，因為要記 `restart_count` |
| 拉 image | 用註冊時的 exact digest；建立前再 inspect 一次 |

## 常駐容器的生命週期

```
(第一筆 Run 或「預熱」) ──▶ STARTING ──存活確認──▶ READY ──停止 / 閒置回收 / 版本被取代──▶ STOPPED
                              │                    │
                              └──起不來或一啟動就退出──▶ FAILED ──重啟──▶ STARTING（restart_count + 1）
```

狀態存在 `agent_containers`（[postgresql.md](postgresql.md)）。`api` 只寫期望狀態並發任務，實際動作是 worker 的 `zentra.container.apply` 或派工時的冷啟動。

### 啟動（`start_container`）

1. 先銷毀殘骸：用 DB 的 Docker id **加上固定容器名**再補刀一次（上次啟動若在建立後、寫回 DB 前失敗，DB 是空的但 Docker 有一個同名容器，會撞名 409）。
2. 發 container token（TTL 30 天，綁 `agent_id + container_uuid`）。
3. pull exact digest → 建 `SERVE` 容器。
4. **等 2 秒再確認它還活著**：`READY` 表示**實際確認過還在跑**，不是「建立指令沒報錯」。一啟動就退出 → `FAILED`，`error` 帶容器自己的輸出（使用者唯一的線索）。
5. 失敗時撤銷剛發的 token。

### 取工作：容器拉，平台不推

容器 long-poll `GET /internal/agents/{agent_id}/work`（25 秒）。選「拉」的理由是**隔離，不是效能**：
「推」要求容器監聽連線，會打破「容器不開 port、只有一個對外方向」—— 那正是 L1 最容易驗證的性質。

- container token **只能**打這一條，打 `/internal/runs/*`、`/llm/*`、`/egress/*`、`/notify/*` 一律 `403`。
  它按字面是長期憑證，但權限窄到洩漏也沒用：最壞是搶到自己 Agent 的工作。
- 每次取工作都更新 `last_health_at`（`STARTING` / `UNHEALTHY` 會被翻成 `READY`）：**能來要工作就是最好的健康訊號**，不另做 `/healthz`。
- 每筆工作項帶**那一筆 Run 的** run token；容器啟動時沒有任何 run token。

### 停止、重啟、回收

| 動作 | 做什麼 |
|---|---|
| 停止 | 撤銷**這一個容器**的 token → 銷毀 → `STOPPED`、`inflight_runs = 0` |
| 重啟 | 同上，再走一次啟動；`restart_count + 1`（一直重啟的容器是壞掉了，這個數字讓它看得見） |
| 閒置回收 | beat 每 60 秒，見 [dispatcher-resources.md](dispatcher-resources.md) §回收 |
| 版本被取代 | `zentra.version.retire`，等綁著舊版本的 Run 都結束再停（[agent-registry.md](agent-registry.md)） |
| 刪 Agent | `zentra.agent.purge` 逐一停掉 |

撤銷一律**以容器為單位**：照 Agent 撤會把兄弟容器的 token 一起撤掉，它們還在 long-poll 卻每次 401，畫面上看起來完全正常。

### 容器綁定單一版本

一個常駐容器只服務一個 `agent_version_id`。發新版 / 取代版本後，新 Run 走新版本的容器，舊容器跑完手上的工作後被收掉 —— 切換期間兩者並存。
工作佇列因此是一個版本一條（[redis.md](redis.md)）。

## 已知缺口

- **沒有存活監控。** `UNHEALTHY` 是保留值，**沒有任何路徑會寫入它**。常駐容器啟動後若死掉（OOM、程式崩潰），
  DB 仍是 `READY`，新工作照樣派進它的佇列卻沒人取，直到逾時清掃收成 `SANDBOX_TIMEOUT`、或閒置回收把它標 STOPPED。
- 常駐容器裡卡死的 Run（`async def` 裡做阻塞呼叫）不會被殺，只有帳面上被逾時清掃收掉。
- ZEN008-ISO-01（`VOLUME` 逃逸），見上。
