# 任務看板 (Task Board)

> **唯一的任務真相。** 所有 agent 開工前看這裡，收工後更新這裡。
> 狀態機：`BACKLOG → SPEC_READY → IN_PROGRESS → REVIEW → QA → DONE`（任何階段可轉 `BLOCKED`）
> 規則：只有 `@architect` 能推進到 `SPEC_READY`；只有 `@qa-verifier` 能推進到 `DONE`。
>
> **對齊 SPEC `v0.2.1`（降維後）。** 舊編號 ZEN-001～ZEN-011 已作廢並重排，見 §已作廢。

## 八週路線圖

一條主線，八週。**每一週都要有東西能跑**，不是八週後一次整合。

| 週 | 里程碑 | 任務 |
|---|---|---|
| W1–2 | 一條 curl 打得通的路徑 | ZEN-000 |
| W2–3 | 上傳自己的程式碼就能跑 | ZEN-001 |
| W3–4 | Agent 能呼叫 LLM，且金鑰不進沙盒 | ZEN-002 |
| W4 | 隔離四層有自動化證據 | ZEN-003 |
| W4–5 | Run API 撐得住失敗與取消 | ZEN-004 |
| W5–6 | MCP 門面與 REST 同源 | ZEN-005 |
| W6–7 | 看得見的 Console | ZEN-006 |
| W7–8 | 收尾、驗收、demo | ZEN-007 |

## 進行中

| ID | 任務 | 狀態 | 負責 | 依賴 | 交接單 |
|---|---|---|---|---|---|
| **ZEN-000** | **貫穿骨架**：API → Celery → 沙盒 → trivial LangGraph → SSE 串流 | `SPEC_READY` | @platform-eng + @backend-dev | — | [spec](../specs/features/ZEN-000/spec.md) |
| ZEN-001 | **版本發布**：上傳 bundle → uv venv → 驗證沙盒抽 entrypoints → `PUBLISHED`（P1 + P5） | `BACKLOG` | @architect → @backend-dev + @platform-eng | ZEN-000 | — |
| ZEN-002 | **SDK v1 + Internal Gateway**：`ctx.llm` / `ctx.logger` / `ctx.emit`，OpenAI 相容 LLM 代理（P4） | `BACKLOG` | @backend-dev | ZEN-001 | — |
| ZEN-003 | **沙盒四層隔離硬化**：internal 網段、cap_drop、cgroups、唯讀 + `capabilities()` | `BACKLOG` | @platform-eng | ZEN-000 | — |
| ZEN-004 | **Run API 完整版**：cancel、列表分頁、SSE `Last-Event-ID` 續傳、逾時、冪等 | `BACKLOG` | @backend-dev | ZEN-000 | — |
| ZEN-005 | **Interface Generator**：一份 entrypoints → OpenAPI + MCP tool schema（P3） | `BACKLOG` | @backend-dev | ZEN-001 | — |
| ZEN-006 | **Console 五個畫面**（原生 HTML/CSS/ES modules，零建置）：Agents 列表 / Agent 詳情 / 上傳新版本 / Runs 列表 / Run 觀測 | `BACKLOG` | @ui-designer → @frontend-dev | ZEN-004 | — |
| ZEN-007 | **收尾與驗收**：隔離回歸測試、失敗路徑、`zentra` CLI、demo 腳本、README | `BACKLOG` | @qa-verifier + 全員 | ZEN-002…006 | — |

> **順序說明**：ZEN-000 刻意排第一，目的是**盡早證偽架構假設**而非交付功能。
> 它一次碰到沙盒生命週期、Celery 冪等、平台注入 compile、事件串流四個高風險點。
> ZEN-003（隔離硬化）與 ZEN-004（Run API）不相依，可平行。

## 被阻塞 (BLOCKED)

| ID | 阻塞原因 | 需要誰解 | 開始阻塞 |
|---|---|---|---|
| — | — | — | — |

## SPEC-QUESTION（規格提問）

> 規格不清楚時開在這裡，**不要自己決定**。由 `@architect` 裁決後收斂回 `specs/SPEC.md`。

| # | 提問 | 由 | 狀態 | 裁決 |
|---|---|---|---|---|
| Q-001 | 範例：Run 逾時後，已經產生的部分輸出要保留還是丟棄？ | @backend-dev | `OPEN` | — |

**提問格式**（新增時照抄）：
```
| Q-00X | <具體問題，附上為什麼卡住> | @<你> | OPEN | — |
```
提問後請同時把相關任務標為 `BLOCKED`，並繼續做不相依的部分。

## 刻意不做（不要自己加回來）

依 [ADR-0005](../specs/adr/ADR-0005-mvp-single-host-scope.md) 與 [SPEC §11](../specs/SPEC.md)，
下列項目**不在八週內做**。發現「順手可以加」→ 記在這裡，不要動手（鐵則 6）。

| 項目 | 為什麼不做 |
|---|---|
| 平台組裝式 Agent（Console 精靈） | 第二條建立路徑，八週做不完 |
| 知識庫 / Ingestion / Hybrid Search | 整個知識層移出 MVP |
| Tool Registry / Skill Registry | 使用者可在自己的 Agent 程式碼裡寫工具 |
| HITL resume / `WAITING_INPUT` | 狀態機已移除該值 |
| Agent Flow 視覺化編排 | 原本就是 MVP 外 |
| 多租戶 / RLS / Alias 流量切換 | 單租戶 |
| Kubernetes / gVisor / Warm Pool / CVE 掃描 | 單機 + 信任的程式碼 |
| React / Vue / Alpine / htmx / bundler / TypeScript / npm | 前端是原生三件套，零建置（[ADR-0006](../specs/adr/ADR-0006-vanilla-frontend.md)） |

## 已作廢的舊編號

v0.1 的編號在降維後重排。**舊交接單或註解提到這些編號時，對照這張表**：

| 舊 ID | 舊任務 | 現況 |
|---|---|---|
| ZEN-001 | 純骨架 | 已於 v0.1 併入 ZEN-000 |
| ZEN-002 | Manifest schema 與 Registry 資料模型 | → 併入新的 ZEN-001 |
| ZEN-003 | 沙盒五層隔離（K8s driver + gVisor） | → 新的 ZEN-003，降為四層 Docker 隔離 |
| ZEN-004 | Celery 多佇列 tier + 死信 + Beat | 作廢（兩條佇列，無 Beat） |
| ZEN-005 | Zentra SDK v1 | → 新的 ZEN-002 |
| ZEN-006 | Run API 完整版 | → 新的 ZEN-004 |
| ZEN-007 | Interface Generator | → 新的 ZEN-005 |
| ZEN-008 | Console Run 觀測頁 | → 併入新的 ZEN-006 |
| ZEN-009 | 知識庫 Ingestion + Hybrid Search | 作廢（MVP 外） |
| ZEN-010 | 沙盒安全驗證套件（CI 上跑） | → 併入新的 ZEN-003 與 ZEN-007 |
| ZEN-011 | Build pipeline（CVE / License 掃描） | 作廢；發布流程併入新的 ZEN-001，不含掃描 |

## 已完成

| ID | 任務 | 完成日 | 驗證者 |
|---|---|---|---|
| — | — | — | — |
