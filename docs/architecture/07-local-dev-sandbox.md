# 本機開發：沙盒怎麼跑（macOS / Colima）

> 對應 [ADR-0004](../../specs/adr/ADR-0004-sandbox-driver-and-dev-environment.md)。
> 本文的實測結果於 2026-09-20 在 Colima 0.8.1 / Docker 27.4.0 / linux/arm64 上驗證。

## 一句話回答

| 問題 | 答案 |
|---|---|
| 沙盒技術是什麼？ | **正式環境**：Kubernetes + gVisor(runsc)。**本機**：Docker + runc + 隔離參數 |
| Mac 上跑得起來嗎？ | **可以**，Colima 已經夠用。L1/L3/L4/L5 都生效，只缺 L2(gVisor) |
| docker compose 就夠了嗎？ | **平台服務：是。沙盒：不是**（見下節） |

---

## 為什麼沙盒不能寫進 docker compose

這是最常見的誤解，值得講清楚：

| | docker compose | 沙盒 |
|---|---|---|
| 何時建立 | `compose up` 時，**一次** | 每次有人呼叫 Agent，**一次一個** |
| 活多久 | 一直活著 | 跑完即銷毀（秒～分鐘） |
| 數量 | 固定 | 隨流量變動，可能同時 0 個或 50 個 |
| 誰決定 | 你寫在 YAML 裡 | **程式執行期決定** |

compose 是宣告「**開機就要跑、一直在那的服務**」。
沙盒是「**使用者打一次 API 才開一個、跑完就刪掉**」——這是程式邏輯，YAML 表達不了。

### 正確的分工

```
docker compose 負責（長駐）:          Sandbox Manager 負責（動態）:
  postgres                              每個 Run → Docker API create
  redis                                 → start → 串流事件
  rabbitmq                              → destroy
  minio
  api        (FastAPI)                 ← 這些容器不在 compose 檔裡
  worker     (Celery)
```

`runner-worker` 掛載 `docker.sock`，透過 Docker API 動態建立沙盒容器：

```python
# apps/sandbox/drivers/docker_driver.py（示意）
container = docker_client.containers.run(
    image=f"zentra/agent:{agent_version.image_digest}",
    detach=True, remove=False,          # 我們自己 destroy，才能收 exit code
    read_only=True,                                    # L4
    tmpfs={"/workspace": "rw,size=512m,mode=1777"},    # L4
    network_mode="zentra-sandbox-net",                 # L1（正式環境換 NetworkPolicy）
    mem_limit="4g", memswap_limit="4g",                # L3
    pids_limit=256, nano_cpus=2_000_000_000,           # L3
    cap_drop=["ALL"],                                  # L2（部分）
    security_opt=["no-new-privileges"],                # L2（部分）
    user="65534:65534",                                # L2（部分）
    environment={"ZENTRA_RUN_TOKEN": short_lived_token},  # TTL 60s，P4
)
```

---

## 本機實測結果（2026-09-20）

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

**結論：本機 Docker 能提供 L1（基本）、L2（部分）、L3、L4 的隔離。**

### 本機拿不到的是什麼

| 缺什麼 | 為什麼 | 影響 |
|---|---|---|
| **L2 gVisor 核心隔離** | Colima 預設沒裝 `runsc`（可自行安裝，見文末） | **容器逃逸類的攻擊本機測不到** |
| **L1 NetworkPolicy** | 那是 K8s 概念，Docker 只能用 `--network none` 或自訂 bridge 近似 | 細緻的網域白名單要到 K8s 才驗得到 |
| **seccomp 白名單** | Docker 預設 profile 夠用，但不是我們自訂的那份 | 系統呼叫層面的收斂要到 K8s |

> ⚠️ **因此：本機的安全測試結果不能作為 G4 放行依據。**
> `SandboxDriver.capabilities()` 必須誠實回報缺 L2，對應測試標 `skipped` 而非 `passed`。
> 只有 CI（x86_64 + Kubernetes + gVisor）的結果算數。這條寫在 `.claude/agents/qa-verifier.md`。

---

## 本機開發環境長怎樣

```bash
# 1. 平台服務（長駐）
docker compose up -d          # postgres, redis, rabbitmq, minio

# 2. API
uv run uvicorn apps.api.main:app --reload

# 3. Worker（需要 docker.sock 才能建沙盒）
export DOCKER_HOST=unix://$HOME/.colima/default/docker.sock
export ZENTRA_SANDBOX_DRIVER=docker
uv run celery -A apps.worker.celery_app worker -Q q.agent.short -l info

# 4. 先建好沙盒映像
docker build -t zentra/agent:dev -f docker/agent-runtime.Dockerfile .
```

### ⚠️ docker.sock 的安全注意

本機 worker 需要 `docker.sock`，而**能操作 docker.sock 等同 root**（可以掛載宿主檔案系統）。
本機開發可接受，**正式環境絕對禁止** —— 正式用 `KubernetesSandboxDriver` + 受限 RBAC
（只能在 `zentra-sandbox` namespace 建刪 Pod）。

`DockerSandboxDriver` 必須在 `ZENTRA_ENV=production` 時**拒絕初始化**，這是硬性檢查不是設定選項。

---

## 想在本機也測 gVisor？（選配，非必要）

Colima 跑的是真的 Linux VM，所以理論上可以在 VM 內安裝 `runsc`：

```bash
colima ssh
# 在 VM 內安裝 runsc，並在 /etc/docker/daemon.json 註冊 runtime
# 然後 docker run --runtime=runsc ...
```

**前置條件檢查（本機實際狀況）**：

| gVisor 需求 | 你的 Colima | 結果 |
|---|---|---|
| Linux 5.6+ | Linux 6.8.0 | ✅ |
| x86_64 或 ARM64 | aarch64 | ✅ 官方支援 |
| 攔截機制 | systrap（2023 年起的預設，不需 KVM，適合 VM 環境） | ✅ |

**所以技術上裝得起來。但仍不建議把它列為開發的前提條件**，原因：
1. **本機 arm64、正式環境 x86_64** —— 本機過了不代表正式環境會過，真正的驗證還是得在 CI 做
2. Colima VM 的變更在 `colima delete` 後會消失，團隊每個人都要重裝
3. 多一個本機依賴 = 多一道新人 onboarding 的摩擦，換來的是重複 CI 已經在做的事

**正確做法**：把 gVisor 驗證放在 CI 的 x86_64 Linux runner（kind + gVisor），那才是有意義的驗證環境。
本機當成一個 30 分鐘的 spike 試試無妨，但不該擋開發。
