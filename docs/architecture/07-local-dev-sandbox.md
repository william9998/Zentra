# 執行環境：沙盒怎麼跑

> 對應 [ADR-0005](../../specs/adr/ADR-0005-mvp-single-host-scope.md)。
> 本文的實測結果於 2026-09-20 在 Colima 0.8.1 / Docker 27.4.0 / linux/arm64 上驗證。
>
> **v0.2.1 起，本機與正式是同一套 docker compose（4 個服務）。** 這份文件不再是「本機開發的特殊做法」，
> 它描述的就是執行環境本身。v0.1 的「本機 Docker / 正式 Kubernetes」分層已經取消。

## 一句話回答

| 問題 | 答案 |
|---|---|
| 沙盒技術是什麼？ | **Docker + 四層隔離參數**（`DockerSandboxDriver`）。無 gVisor |
| Mac 上跑得起來嗎？ | **可以**，Colima 已經夠用，L1/L3/L4/L5 都生效 |
| docker compose 就夠了嗎？ | **平台服務：是。沙盒：不是**（見下節） |
| 正式環境不一樣嗎？ | **一樣。** 同一份 compose，同一個 driver |

---

## 為什麼沙盒不能寫進 docker compose

這是最常見的誤解，值得講清楚：

| | docker compose | 沙盒 |
|---|---|---|
| 何時建立 | `compose up` 時，**一次** | 每次有人呼叫 Agent，**一次一個** |
| 活多久 | 一直活著 | 跑完即銷毀（秒～分鐘） |
| 數量 | 固定 | 隨流量變動，可能同時 0 個或 10 個 |
| 誰決定 | 你寫在 YAML 裡 | **程式執行期決定** |

compose 是宣告「**開機就要跑、一直在那的服務**」。
沙盒是「**使用者打一次 API 才開一個、跑完就刪掉**」——這是程式邏輯，YAML 表達不了。

### 正確的分工

```
docker compose 負責（長駐，4 個）:      Sandbox Manager 負責（動態）:
  postgres                               每個 Run → Docker API create
  redis                                  → start → 收事件
  api     (FastAPI + Console 靜態檔)      → destroy
  worker  (Celery)                     ← 沙盒不在 compose 檔裡
```

> **Console 沒有自己的容器**（[ADR-0006](../../specs/adr/ADR-0006-vanilla-frontend.md)）：
> 它是 `apps/console/` 底下的原生 HTML / CSS / ES modules，由 `app` 用 `StaticFiles` 服務。
> 沒有建置步驟 —— 改一行 CSS 重新整理就看到。

`worker` 掛載 `docker.sock`，透過 Docker API 動態建立沙盒容器：

```python
# apps/sandbox/drivers/docker_driver.py（示意）
container = docker_client.containers.run(
    image="zentra/agent-runtime:1.0",   # 共用映像，不是 per-agent build
    detach=True, remove=False,          # 我們自己 destroy，才能收 exit code
    read_only=True,                                    # L4
    volumes={                                          # L4
        f"{DATA}/bundles/{version_id}": {"bind": "/app/code", "mode": "ro"},
        f"{DATA}/venvs/{version_id}":   {"bind": "/app/venv", "mode": "ro"},
    },
    tmpfs={"/workspace": "rw,size=512m,mode=1777"},    # L4
    network="zentra-sandbox-net",                      # L1（internal: true）
    mem_limit="2g", memswap_limit="2g",                # L3
    pids_limit=256, nano_cpus=2_000_000_000,           # L3
    cap_drop=["ALL"],                                  # L2
    security_opt=["no-new-privileges"],                # L2
    user="65534:65534",                                # L2
    environment={                                      # P4
        "ZENTRA_RUN_TOKEN": run_token,                 # TTL = run timeout
        "ZENTRA_GATEWAY": "http://app:8000/internal",
        "ZENTRA_RUN_ID": run_id,
    },
)
```

**注意這裡沒有任何長期憑證**（P4）。沒有 LLM key、沒有 DB 密碼。
沙盒要呼叫模型，走 `ZENTRA_GATEWAY`，金鑰在 `app` 那一側。

---

## 實測結果（2026-09-20）

以上述參數實際在 Colima 上跑過，結果如下：

| 層 | 測試 | 結果 |
|---|---|---|
| L4 檔案 | 寫入 `/` | ✅ 被擋 `Errno 30 Read-only file system` |
| L4 檔案 | 寫入 `/usr/lib` | ✅ 被擋 |
| L4 檔案 | 寫入 `/workspace` | ✅ 可寫（符合預期） |
| L1 網路 | 連 `1.1.1.1:53` | ✅ 被擋 `Errno 101 Network is unreachable` |
| L1 網路 | 連 `169.254.169.254`（雲端 metadata） | ✅ 被擋 |
| L2 權限 | `mount -t proc` | ✅ 被擋（cap_drop ALL 生效） |
| L2 權限 | 執行身分 | ✅ uid/gid = 65534（非 root） |
| L3 資源 | 記憶體炸彈（限 256m） | ✅ 配置到 250MB 即被 OOM killer 殺掉（exit 137） |
| L3 資源 | fork bomb（限 64 pids） | ✅ 在第 63 個 process 被擋下 |

**結論：Docker 能提供 L1、L2（部分）、L3、L4 的隔離。**

### 還沒驗的兩項（ZEN-003 要補）

| 待驗 | 怎麼驗 |
|---|---|
| 沙盒 → `postgres:5432` / `redis:6379` **必須不通** | `internal: true` 網段隔離了外部路由，但同網段內容器互通。要確認 postgres/redis **沒有**接上沙盒網段 |
| 沙盒 → `app:8000/internal` **必須通** | `app` 要同時接上兩個網段，這是唯一的橋 |

---

## 拿不到的是什麼（誠實列出）

| 缺什麼 | 為什麼 | 影響 |
|---|---|---|
| **L2 gVisor 核心隔離** | 沒有安裝 `runsc`，且 ADR-0005 決定不裝 | **容器逃逸類的攻擊擋不住** |
| **沙盒與平台的實體隔離** | 單機，沙盒和 postgres、api 同一台 | 逃逸後鄰居就是憑證。ADR-0001 的「專用 node pool」硬規則在單機上做不到 |

> ⚠️ **因此：這個等級只適用於信任的程式碼。**
> `SandboxDriver.capabilities()` 必須誠實回報 `l2_kernel_isolation: false`，
> 對應測試標 `skipped` 而非 `passed`。
> 要跑任何不是自己寫的 Agent 之前，回去讀 [ADR-0001](../../specs/adr/ADR-0001-sandbox-isolation.md)
> 與 [ADR-0005](../../specs/adr/ADR-0005-mvp-single-host-scope.md) 的「何時該重新檢討」。

---

## 開發環境長怎樣

```bash
# 0. 建立沙盒專用網段（internal，無對外路由）
docker network create --internal zentra-sandbox-net

# 1. 全部服務（postgres / redis / api / worker）
docker compose up -d      # Console 在 http://localhost:8000/console/agents

# 2. 沙盒映像（共用，不是 per-agent）
docker build -t zentra/agent-runtime:1.0 -f docker/agent-runtime.Dockerfile .

# 3. 資料表
make migrate      # = docker compose exec -T app python -m apps.api.migrations（ADR-0015）

# 4. 驗一條主線
uv run zentra deploy ./examples/echo-agent
curl -X POST localhost:8000/v1/agents/<id>/runs -d '{"input":{"text":"hi"}}'
curl -N localhost:8000/v1/runs/<run_id>/events
```

開發時要熱重載，可以把 `app` 與 `worker` 從 compose 拿出來跑在主機上：

```bash
docker compose up -d postgres redis
export DOCKER_HOST=unix://$HOME/.colima/default/docker.sock
uv run uvicorn apps.api.main:app --reload    # 同時服務 Console 靜態檔
uv run celery -A apps.worker.celery_app worker -Q q.run,q.build -l info
```

前端不需要任何額外指令 —— 沒有 dev server、沒有 watch、沒有 `node_modules`。
`--reload` 只影響 Python；改 `.html` / `.css` / `.js` 直接重新整理瀏覽器。

⚠️ 這時 `app` 跑在主機上，沙盒要打得到它——沙盒網段內的容器連不到主機的 `localhost`。
用 `host.docker.internal` 當 `ZENTRA_GATEWAY`，但**那會讓沙盒網段多一條對外路由**，
所以這個模式**只在開發時用**，`ZENTRA_ENV` 不是 `dev` 時 driver 要拒絕。

### ⚠️ `docker.sock` 的風險現在是常駐的

`worker` 需要 `docker.sock`，而**能操作 docker.sock 等同主機 root**（可以掛載宿主檔案系統）。

v0.1 把這條列為「僅限本機開發」。**v0.2.0 的正式環境就是這一套，所以這個風險常駐。**
現在可接受的理由與將來的解法寫在
[detailed-architecture.md §9](detailed-architecture.md)。

**只有 `worker` 掛 `docker.sock`。`app` 不准掛。**
