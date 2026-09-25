# ADR-0005：MVP 降維 —— 單機 docker compose、信任程式碼、單一主線

| | |
|---|---|
| 狀態 | **已採納 (Accepted)** |
| 日期 | 2026-09-20 |
| 決策者 | @architect（依專案負責人裁示） |
| 相關 | 取代 [ADR-0001](ADR-0001-sandbox-isolation.md) 的部署部分、取代 [ADR-0004](ADR-0004-sandbox-driver-and-dev-environment.md) 的環境分層 |

> ⚠️ **前端部分被 [ADR-0006](ADR-0006-vanilla-frontend.md) 再降一階（2026-09-20）。**
> 本 ADR 定的是 **5 個服務**（含 Next.js 的 `console`）。ADR-0006 把前端改成
> **原生 HTML + CSS + ES modules、零建置**，`console` 容器取消、由 `api` 服務靜態檔，
> 因此**實際是 4 個服務**，且 L0 的 Console BFF 也一併移除。
> 本文下方的服務清單保留當時的樣貌作為紀錄，**現況以 [SPEC.md](../SPEC.md) §6.1 為準**。

> ⚠️ **服務清單再被 [ADR-0009](ADR-0009-internal-registry-image-upload.md) 修訂（2026-09-22）：4 → 5。**
> 新增內部 OCI Registry（`registry:2`），只掛 `zentra-net`、只發布 `127.0.0.1:6000`
> （主機端；容器內仍是 5000，見 ADR-0009 R3）、
> 無 TLS／無認證／無自動 GC。
> **它的用途在 ADR-0009 修訂 R2（2026-09-23）改變**：原本是承接「上傳 image tar」，
> 現在是讓使用者**在平台主機上**直接 `docker push`。服務數維持 5，平台不在 push 路徑上。
> **這是對本 ADR 服務清單的修訂，不是例外**——理由與逐條重新定價寫在 ADR-0009。
> 本 ADR 的其餘決定（單機、無 K8s、無 gVisor、信任程式碼、一條主線）**一個字都沒改**。

## 背景

原架構（ADR-0001 + ADR-0004）是為「**公開多租戶平台、執行不可信的陌生人程式碼**」設計的。
在那個威脅模型下，gVisor 專用節點池、三個 Proxy、Vault、CVE 掃描、Warm Pool 全都是必要成本。

但這個專案實際的落地條件是：

| 條件 | 值 |
|---|---|
| 使用場景 | **內部工具** |
| 租戶 | **單一租戶** |
| 程式碼來源 | **只跑自己信得過的程式碼** |
| 交付目標 | **MVP demo** |
| 時程 | **2 個月** |

威脅模型變了，原架構就從「必要成本」變成「為不存在的威脅付錢」。
同時，兩個月要交付的瓶頸不在基礎設施，而在**功能範圍**：原 SPEC §11 的 MVP 清單
（上傳式 + 平台組裝式 + Tool Registry + 知識庫 Hybrid Search + REST + MCP + Console）八週做不完。

不處理的結果是可預期的：八週後有一個半成品 K8s 部署，和一條從來沒有從頭跑通過的主線。

## 決定

**三刀，同時下。**

### 一、部署降維：K8s → 單機 docker compose

正式環境與開發環境**是同一套 docker compose**，不再有「本機一套、正式一套」的分歧。

```
docker compose 長駐（5 個服務）        Sandbox Manager 動態建立
  postgres   ← 關聯資料 + 事件           sandbox-<run_id>
  redis      ← Celery broker + Streams   （每 Run 一個，結束即銷毀，
  api        ← REST · MCP · SSE ·          不寫在 compose 檔裡）
               Console BFF · Internal
               Gateway
  worker     ← Celery
  console    ← Next.js
```

物件儲存用**本機 volume**（`data/bundles/`、`data/venvs/`），透過 `ObjectStore` 介面存取，
之後要換 S3 只換實作。

### 二、基礎設施降維：砍掉七項

| 砍掉 | 原本做什麼 | 現在怎麼做 |
|---|---|---|
| **RabbitMQ** | Celery broker | Redis 同時當 broker + result + Streams |
| **OpenSearch** | BM25 全文檢索 | 知識庫不在 MVP；之後用 Postgres `tsvector` + pgvector 做 Hybrid |
| **MinIO** | bundle / artifact | 本機 volume + `ObjectStore` 介面 |
| **ClickHouse** | 用量分析 | Postgres 的 `llm_calls` 表 |
| **Vault** | 憑證保管 | compose 的 env + Docker secret；**沙盒仍然拿不到**（見下） |
| **Tool / LLM / Egress 三個 Proxy** | 沙盒的受控出口 | 合併成 **Internal Gateway**——`api` 服務上的一組 `/internal/*` 路由 |
| **Warm Pool、per-agent image build、CVE/License 掃描** | 冷啟優化、供應鏈防護 | 單一共用 runtime image + per-version venv 目錄；信任程式碼不需要供應鏈掃描 |

**Internal Gateway 是這一刀的關鍵**。沙盒的 docker network 設 `internal: true`（完全沒有對外路由），
`api` 容器同時接上沙盒網段與預設網段，成為沙盒**唯一**的網路對端：

| 路由 | 用途 |
|---|---|
| `POST /internal/llm/v1/chat/completions` | OpenAI 相容。平台注入真 key、計量 token、寫稽核 |
| `POST /internal/runs/{run_id}/events` | 沙盒事件回寫（沙盒**不得直連** Redis 或 Postgres） |
| `GET /internal/runs/{run_id}/bootstrap` | 取 manifest 與 input |

認證用 **run token**：綁定單一 `run_id`，TTL = 該 Run 的 timeout，跑完立即撤銷。
**P4 完整成立** —— 沙盒內找不到任何長期憑證，且零個額外容器。

### 三、範圍降維：只留一條主線

**MVP 內（唯一主線）**

```
上傳 Python → 發布成不可變版本 → 沙盒執行 → 自動 REST + MCP → Run 觀測（SSE）
```

**MVP 外（架構留位，不寫程式碼）**：平台組裝式 Agent、知識庫 / Hybrid Search、
Tool Registry、HITL resume、Agent Flow、多租戶 RLS、Alias 流量切換、排程觸發。

### 四、沙盒隔離：五層 → 四層，並且誠實標示

| 層 | MVP 做什麼 | 狀態 |
|---|---|---|
| L1 網路 | `internal: true` bridge，唯一對端是 Internal Gateway | ✅ 完整 |
| L2 核心 | `cap_drop ALL`、`no-new-privileges`、non-root(65534)、Docker 預設 seccomp | ⚠️ **無 gVisor** |
| L3 資源 | mem / pids / cpu / wall-clock timeout / stdout 上限 | ✅ 完整 |
| L4 檔案 | 唯讀 rootfs、`/app` ro bind、`/workspace` tmpfs、無 hostPath | ✅ 完整 |
| L5 應用 | SDK 攔截層、禁自建 LLM client | ✅ 完整 |

`SandboxDriver.capabilities()` 必須回報 `l2_kernel_isolation: false`，
安全測試對應項標 **`skipped`，不是 `passed`**（這條從 ADR-0004 原封不動保留）。

**這個等級適用於信任的程式碼，不適用於公開多租戶。** 寫在這裡，不要之後假裝忘記。

## 什麼沒有改變

**五條核心立場 P1–P5 全部保留，一條都沒鬆。** 降的是實作維度，不是立場：

| | 立場 | 在新架構怎麼成立 |
|---|---|---|
| P1 | 使用者程式碼不在平台行程內執行 | worker 只呼叫 Docker API；連「驗證 `build()` 是否存在」都在一次性沙盒裡做，因為 import 使用者模組就是執行使用者程式碼 |
| P2 | 統一 LangChain + LangGraph | 不變 |
| P3 | API + MCP 雙門面 | 不變。MCP 用 FastMCP 掛在同一個 FastAPI app |
| P4 | 沙盒內無長期憑證 | 三個 Proxy → 一個 Internal Gateway，run token TTL = run timeout |
| P5 | 版本不可變 | image digest → **bundle SHA-256**。發布後 bundle 目錄與 venv 目錄設唯讀 |

`SandboxDriver` 介面抽象（ADR-0004 §一）也完整保留 —— 它就是之後要回到 K8s 時的逃生門。

## 考慮過但否決的選項

- **k3s 單機**
  否決理由：技術上更好（RuntimeClass / NetworkPolicy / RBAC / taint 全都有，將來擴多機不用重寫）。
  但對「內部工具 + 信任程式碼」而言，它解決的問題目前不存在，卻要付出團隊學 K8s 的時間。
  **這是目前最接近的替代方案，若威脅模型改變，優先考慮它。**

- **只砍基礎設施、功能全留**
  否決理由：八週做不完。硬排的結果是每一項都做到 60%，沒有一條路徑能跑通。

- **放棄沙盒，直接在 worker 行程內 exec**
  否決理由：這會拿掉 P1，而 P1 是這個產品的核心技術，不是可選的安全加值。
  沙盒在 Docker 上的成本只有幾天，省它不划算。

## 後果

### 正面
- 基礎設施從 11 個服務降到 5 個；分層從 L0–L6 七層降到四層
- 本機 = 正式，不再有「本機過了正式不一定過」的驗證斷層（ADR-0004 最大的痛點消失）
- 八週有機會真的交付一條跑得通的主線

### 負面（誠實列出）
- **單機就是單點故障。** 無 HA、無滾動更新、無水平擴縮。主機重開 = 停機。
  NFR 的可用性目標因此**拿掉**，不是降低到 99%，是明確不設目標。
- **沒有 gVisor，擋不住核心層的容器逃逸。** 只要跑了不可信的程式碼，這個架構就不夠。
- **容量上限由單機決定。** 併發 Run 預設壓到 10，由 Sandbox Manager 的 semaphore 控，超過回 `QUOTA_EXCEEDED`。
- **砍掉的功能之後要補回來。** 知識庫、Tool Registry、HITL 的資料表與 API 都沒有預先建，
  補的時候會有 migration 與契約 bump。這是刻意的取捨——預先建一個沒人用的抽象，比之後補還貴。

### 何時該重新檢討

任何一條成立，就回頭讀 ADR-0001 與 ADR-0004，並優先評估 k3s：

- 要跑**非自己撰寫**的 Agent 程式碼（哪怕只有一個）
- 出現**第二個租戶**
- 需要可用性承諾，或單機容量不夠
- 要對外商用
