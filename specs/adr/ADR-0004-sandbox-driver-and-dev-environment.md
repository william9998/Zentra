# ADR-0004：SandboxDriver 抽象與開發／正式環境分層

| | |
|---|---|
| 狀態 | **已採納 (Accepted)** |
| 日期 | 2026-09-20 |
| 決策者 | @architect |
| 相關 | [ADR-0001](ADR-0001-sandbox-isolation.md)（隔離策略）、立場 P1、P4 |

> ⚠️ **環境分層部分被 [ADR-0005](ADR-0005-mvp-single-host-scope.md) 取代（2026-09-20）。**
> `SandboxDriver` 介面抽象與 `capabilities()` 誠實回報原則**完整保留**，是本 ADR 最有價值的部分。
> 但「本機 Docker / 正式 Kubernetes」的分層在 MVP **不成立**——正式環境就是 docker compose，
> 本機與正式是同一套，`KubernetesSandboxDriver` 不在 MVP 範圍。
> 「`DockerSandboxDriver` 在 production 拒絕初始化」這條硬性檢查因此**取消**，改由 ADR-0005 的
> 四層隔離清單與 `capabilities()` 把關。
>
> **介面也少一個方法。** 下方 §一 的 `stream_events()` 是 v0.1 的設計（worker 從沙盒串事件）。
> ADR-0005 之後事件改走 **Internal Gateway**（沙盒 POST 回 `api`），driver 不再碰事件。
> 現行介面是：`create` · `wait` · `logs` · `destroy`（冪等）· `capabilities`。

## 背景

ADR-0001 決定用 **gVisor + 五層縱深防禦**。但落地時撞到兩個現實問題：

1. **開發機是 macOS / ARM64。** 開發環境為 Colima 0.8.1（Virtualization.Framework）起的 Linux VM，
   Docker 27.4.0 `linux/arm64`，目前只註冊 `runc` runtime，沒有 `runsc`。
   Colima VM 核心為 Linux 6.8.0 / aarch64，**符合 gVisor 的 Linux 5.6+ 需求，且 gVisor 官方支援 ARM64**，
   所以本機技術上裝得起 runsc。真正的問題不是「裝不裝得起來」，而是
   **本機 arm64 與正式環境 x86_64 的架構不一致** —— 本機驗證過不等於正式環境驗證過。
2. **沙盒不是 docker compose 服務。** compose 描述的是一組長駐服務；
   沙盒是「**每個 Run 動態建立、結束即銷毀、絕不重用**」的短生命週期資源。語意根本不同。

如果不處理，會有兩種失敗：
- 逼本機一定要跑 gVisor → 卡在 ARM64 相容性，開發停擺
- 本機隨便跑、正式才接隔離 → 寫了兩個月才發現安全測試從來沒跑過

## 決定

### 一、`SandboxDriver` 介面抽象

Sandbox Manager 不直接呼叫 Docker 或 K8s，而是透過一組介面：

```python
class SandboxDriver(Protocol):
    async def create(self, spec: SandboxSpec) -> SandboxHandle: ...
    async def wait_ready(self, h: SandboxHandle, timeout: float) -> None: ...
    async def stream_events(self, h: SandboxHandle) -> AsyncIterator[Event]: ...
    async def destroy(self, h: SandboxHandle) -> None: ...      # 必須冪等
    async def capabilities(self) -> DriverCapabilities: ...     # 這個 driver 實際提供哪幾層隔離
```

| Driver | 用途 | 隔離層級 |
|---|---|---|
| `DockerSandboxDriver` | 本機開發 | L1 部分（自訂 bridge + 無直連外網）、L3 cgroups、L4 唯讀掛載、L5 SDK 攔截。**無 L2 gVisor** |
| `KubernetesSandboxDriver` | CI / staging / 正式 | 五層全開（gVisor RuntimeClass + NetworkPolicy + seccomp + cgroups + SDK） |

**`capabilities()` 是這個設計的關鍵**：driver 必須誠實回報自己提供哪幾層。
- 啟動時若 `ZENTRA_ENV=production` 而 driver 回報缺少 L2 → **程式直接拒絕啟動**，不是印個警告就跑下去
- 安全測試讀 `capabilities()` 決定哪些斷言該跑、哪些標記為 `skipped`（**不是 passed**）

### 二、環境分層

| 環境 | Driver | 架構 | 跑得了的驗證 |
|---|---|---|---|
| 本機（Colima） | Docker | arm64 | 功能流程、L3 資源限制、L4 檔案、L5 SDK 攔截 |
| CI（GitHub Actions） | Kubernetes (kind + gVisor) | **x86_64** | **五層完整驗證，含逃逸測試** |
| Staging / 正式 | Kubernetes | x86_64 | 同上 + 真實負載 |

**平台服務**（FastAPI / Celery / Postgres / Redis / RabbitMQ / MinIO）本機一律用 **docker compose**，這部分沒有爭議。
**沙盒**由 Sandbox Manager 經 Docker API 動態建立，**不寫在 compose 檔裡**。

### 三、本機 docker.sock 的風險，明確標記為僅限開發

本機 `DockerSandboxDriver` 需要 `/var/run/docker.sock`。**掛載 docker.sock 等同授予 root 權限** ——
能建立容器就能掛載宿主檔案系統。這在本機可接受，**正式環境絕對禁止**。

因此：
- `DockerSandboxDriver` 在 `ZENTRA_ENV=production` 下**拒絕初始化**（硬性檢查，不是設定選項）
- 正式環境用 `KubernetesSandboxDriver` + ServiceAccount，RBAC 限縮到「只能在 `zentra-sandbox` namespace 建立/刪除 Pod」

## 考慮過但否決的選項

- **把「本機必須跑 gVisor」列為開發前提**
  否決理由：技術上可行（核心版本與架構都符合），但**本機 arm64 與正式 x86_64 不一致**，
  「本機 gVisor 過了」不代表正式環境會過，真正的驗證仍然只能在 CI 做。
  多一個本機前提條件只是增加新人 onboarding 的摩擦。**可以裝來玩，但不該是必要條件。**

- **本機完全不做隔離，等上 K8s 再說**
  否決理由：L3/L4/L5 在 Docker 上做得到，早點做早點暴露問題。而且沒有隔離的本機環境會養成壞習慣。

- **把沙盒也寫進 docker compose**
  否決理由：語意錯誤。沙盒是動態、一次性的資源，compose 無法表達「每個 Run 開一個、結束銷毀」。

## 後果

### 正面
- 開發不被 ARM64 / gVisor 相容性卡住
- `capabilities()` 讓「這個環境沒測到哪幾層」變成**明確資訊**，而不是沈默的假象
- 正式環境換 driver 不需改 Sandbox Manager 的邏輯

### 負面（誠實列出）
- **本機測過 ≠ 正式安全。** L1 網路策略與 L2 核心隔離只有 CI 以上才驗得到。
  這必須寫進 `qa-verifier` 的規則：**本機的安全測試結果不能作為放行依據。**
- 兩套 driver 要各自維護與測試
- 本機 arm64 / 正式 x86_64 的差異可能藏 Python 套件相容性問題（C extension），
  Build pipeline 要以**正式環境的架構**為準做依賴解析

### 何時該重新檢討
- 團隊實測 Colima + runsc(arm64) 穩定，且願意承擔多一個本機依賴（屆時本機可補上 L2）
- 正式環境改用 ARM64 節點（架構不一致的問題消失）
- 出現比 docker.sock 更安全的本機動態容器方案
