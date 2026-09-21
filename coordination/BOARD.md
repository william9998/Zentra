# 任務看板 (Task Board)

> **唯一的任務真相。** 所有 agent 開工前看這裡，收工後更新這裡。
> 狀態機：`BACKLOG → SPEC_READY → IN_PROGRESS → REVIEW → QA → DONE`（任何階段可轉 `BLOCKED`）
> 規則：只有 `@architect` 能推進到 `SPEC_READY`；只有 `@qa-verifier` 能推進到 `DONE`。
>
> **對齊 SPEC `v0.2.2`（降維後）。** 舊編號 ZEN-001～ZEN-011 已作廢並重排，見 §已作廢。

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
| **ZEN-000** | **貫穿骨架**：API → Celery → 沙盒 → trivial LangGraph → SSE 串流 | `REVIEW` | @platform-eng + @backend-dev；@qa-verifier 驗收 | — | [spec](../specs/features/ZEN-000/spec.md) · [acceptance](../specs/features/ZEN-000/acceptance.md) |
| ZEN-001 | **版本發布**：上傳 bundle → uv venv → 驗證沙盒抽 entrypoints → `PUBLISHED`（P1 + P5） | `BACKLOG` | @architect → @backend-dev + @platform-eng | ZEN-000 | — |
| ZEN-002 | **SDK v1 + Internal Gateway**：`ctx.llm` / `ctx.logger` / `ctx.emit`，OpenAI 相容 LLM 代理（P4） | `BACKLOG` | @backend-dev | ZEN-001 | — |
| ZEN-003 | **沙盒四層隔離硬化**：internal 網段、cap_drop、cgroups、唯讀 + `capabilities()` | `BACKLOG` | @platform-eng | ZEN-000 | — |
| ZEN-004 | **Run API 完整版**：cancel、列表分頁、SSE `Last-Event-ID` 續傳、逾時、冪等 | `BACKLOG` | @backend-dev | ZEN-000 | — |
| ZEN-005 | **Interface Generator**：一份 entrypoints → OpenAPI + MCP tool schema（P3） | `BACKLOG` | @backend-dev | ZEN-001 | — |
| ZEN-006 | **Console 五個畫面**（原生 HTML/CSS/ES modules，零建置）：Agents 列表 / Agent 詳情 / 上傳新版本 / Runs 列表 / Run 觀測 | `BACKLOG` | @ui-designer → @frontend-dev | ZEN-004 | — |
| ZEN-007 | **收尾與驗收**：隔離回歸測試、失敗路徑、`zentra` CLI、demo 腳本、README | `BACKLOG` | @qa-verifier + 全員 | ZEN-002…006 | — |

> **ZEN-000 現況（2026-09-21）**：四服務 compose、固定 Agent 的 API → Celery → 沙盒 → SSE 主線已跑通。
> 主 session 重建映像後執行公開 API 測試 **10 passed**、Gateway DB 整合 **3 passed**，
> 並執行修正版 [`scripts/verify-zen000.sh`](../scripts/verify-zen000.sh) 得 **27 通過／0 失敗／2 skipped**。
> QA 另在 Compose 網路執行 **23 passed／1 skipped**，並完成 40 次不同沙盒的冷啟量測：
> `POST → 首筆 node.start SSE` 的 nearest-rank P95 為 **1.341 秒**，40 個沙盒終態後皆不存在。
> skipped 是 gVisor 核心隔離與容器逃逸測試，不能算通過（[ADR-0005](../specs/adr/ADR-0005-mvp-single-host-scope.md)）。
> `coordination/DRIFT.md` 的首次 G1 裁定不通過；後端、平台及驗收腳本已修正列出的漂移，
> 但需 spec-keeper 重驗；真實故障注入與逾時容器清理等驗收項仍待證據。
> **維持 `REVIEW`，不得宣稱 `DONE`。** 詳見 [acceptance.md](../specs/features/ZEN-000/acceptance.md)。

> **ZEN-006A 驗收裁定（2026-09-22，@qa-verifier）：`DONE`。**
> 原為「有條件通過」，唯一阻擋是 `CHANGELOG.md` 缺條目（G5）；主 session 已補上三條，QA 明示補完即 `DONE`、不需複驗。
> 證據：`tests/zen006a` **141 passed / 1 xfailed**（主 session 獨立複跑數字一致）；
> `tests/zen000` 在 QA 以 socat 把容器的 postgres／redis 橋進主機後 **23 passed / 1 skipped**
> —— 先前 10 條 DB 測試的 skip 是主機原生 Postgres 佔用 `127.0.0.1:5432` 造成，不是程式問題。
> 三條契約偏離經 QA 逐條對照事前門檻，**判定不阻擋**，但**移交條件**：
> ZEN-006 在 @ui-designer / @architect 裁決這三條之前**不得進 `SPEC_READY`**。
>
> **已知缺陷 `ZEN-006A-D1`（次要，不阻擋，未修）**：RUNNING 期間「已耗時」可能顯示 `—`，最長持續到下一個 1 秒 tick。
> 成因是 `apps/console/static/js/pages/run.js` 的 `elapsedMs()` 用伺服器時間戳減瀏覽器 `Date.now()`，
> 時鐘偏移（實測 `created_at` 快 +0.017s）使其為負，`dom.js` 的 `formatDuration()` 回 `—`。
> 一行可修（`Math.max(0, …)`）。`tests/zen006a` 已用 `xfail` + `KNOWN_DEFECTS` 釘住，修好會變 `XPASS`。
>
> **未覆蓋，不得當成通過**：Safari／Firefox、窄螢幕、螢幕報讀器、`prefers-reduced-motion`、剪貼簿實際寫入、
> 對比度量測、`token`／`log`／`custom` 的真實資料、**真實的 FAILED／CANCELLED Run**（該兩畫面是以 CDP 注入合成回應渲染）、
> `Last-Event-ID` 續傳、負載測試、G1 的 spec-keeper 覆核。沙盒 L1–L4 本次未重跑（diff 半徑未觸及沙盒）。
> 裁定全文：[qa-verifier 交接單](handoffs/ZEN-006A/qa-verifier-to-orchestrator.md)｜驗收條件：[acceptance.md](../specs/features/ZEN-006A/acceptance.md)

> **ZEN-006A 整合驗證（2026-09-21，由主 session 執行）**：三個角色皆已交件，主 session 重建 `zentra-api` 映像後，
> 對**跑著的 compose 環境 + headless Chrome** 實測完整流程（填 question → 送出 → 導頁 → 事件流跑完），**16/16 通過**。
> 重點證據：終態後 `/events` 請求數 = 1（`EventSource` 未重連重播）、注入 `<img src=x onerror>` 後 DOM 內 `img` 數 = 0、
> 0 未捕捉例外 / 0 `console.error`、無水平捲動、`/openapi.json` 無 `/console` 洩漏。
> `tests/zen000/` 對活的服務為 **14 passed / 10 skipped**（10 條公開 API 契約測試全過，無退化）。
> 首輪發現 `run_id` 旁複製按鈕標籤直排（量得 13×36），已由 @frontend-dev 修正並對重建後容器複量為 24×18。
>
> **未驗證，不得當成已通過**：Safari / Firefox、窄螢幕、螢幕報讀器、`prefers-reduced-motion`、剪貼簿實際寫入、
> `CANCELLED` 畫面（後端尚無此狀態路徑）、`token` / `log` / `custom` 事件的真實渲染（需 ZEN-002）。
> `test_worker_recovery.py` 與 `test_gateway_db_integration.py` 的 DB 測試在本機仍 skipped：
> 主機有原生 Postgres 佔用 `127.0.0.1:5432`，遮蔽容器的 postgres，從主機連不到測試資料庫。
> 以上為主 session 的整合驗證，**不是 QA 裁定**；`DONE` 仍須 @qa-verifier 依驗收條件判定。

> **ZEN-006A 提前的理由（2026-09-21）**：由**使用者直接授權**，把 ZEN-006 的 Run 觀測頁提前切一小片出來，
> 目的是讓人能在瀏覽器上實際操作 ZEN-000 的骨架，而不是只能打 curl。**這不是把 ZEN-006 往前移**，
> ZEN-006 本身維持 `BACKLOG`、維持依賴 ZEN-004。
>
> 範圍硬上限：**只准用已經存在的三個端點**（`POST /v1/agents/{id}/runs`、`GET /v1/runs/{id}`、
> `GET /v1/runs/{id}/events`）。不做 Run 列表、不做 cancel、不做上傳、不做 MCP、不做另外三個畫面。
>
> **已知的契約偏離，待 @ui-designer 與 @architect 追認**（不得視為已核准）：
> 1. `/` 暫時轉址到 `/console/runs`，非 `ui-contract.md` 表頭寫的 `/console/agents`（該頁此切片不存在）。
> 2. `runs.html` 在 `GET /v1/runs` 上線前恆為 Empty 態，其「發起測試 Run」`<dialog>` 表單不在 `ui-contract.md` §5 的元件清單內。
> 3. `runs.html` 用 `localStorage` 暫存最近 `run_id` 作為鷹架，ZEN-004 的列表端點上線後必須整段移除。
>
> ZEN-006 正式開工前，上述三點要嘛收斂回 `ui-contract.md`，要嘛刪掉。**不得預設它們已成為契約。**

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
| Q-002 | ui-contract §2 要求狀態圓點 6px，但 §1 沒有對應尺寸 token。目前借用 `--radius-sm`（值剛好 6px）。是否新增 `--dot-size: 6px`？ | @frontend-dev | OPEN | — |
| Q-003 | §4 規定 Empty 態要有「一行說明 + 一個 secondary 按鈕」。子區塊（進行中的事件時間軸）沒有合理動作，目前放「重新整理」。子區塊 Empty 可否省略按鈕？ | @frontend-dev | OPEN | — |
| Q-004 | §6.1 檔案清單沒有共用工具模組，實作新增了 `static/js/dom.js`（安全建節點／圖示／計時器登記／複製／時間格式化）。保留，或指定併入哪個既有檔？ | @frontend-dev | OPEN | — |
| Q-005 | §6.2 規定契約欄位名只准出現在 `api.js`。屬性存取已遵守，但畫面把 `run_id` / `llm_calls` / `cost_usd` 當**標籤文字**顯示。標籤文字算不算違規？ | @frontend-dev | OPEN | — |
| Q-006 | 硬性規則 5「不准寫死間距」與 media query 衝突：CSS 變數不能用在 media query 條件裡，且零建置無法預處理。目前全 CSS 只有 `768px` 一個字面值斷點，可否接受？ | @frontend-dev | OPEN | — |
| Q-007 | token 指定 `Inter` / `Outfit` / `Noto Sans TC`，但不引 CDN 也沒有字型檔。自帶字型檔（多幾百 KB 靜態資產）還是接受系統 fallback？目前是 fallback。 | @frontend-dev | OPEN | — |

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

## 開發工具維護（不改產品 MVP 範圍）

| ID | 任務 | 狀態 | 負責 | 交接 |
|---|---|---|---|---|
| DEV-CODEX | Codex 專案入口、七角色對齊、五個流程 skills 與使用說明 | `DONE` | @qa-verifier 裁定；@orchestrator 整合 | [驗收](handoffs/DEV-CODEX/qa-verifier-to-orchestrator.md) |

本項由使用者直接授權的工具設定維護，不屬於 ZEN 產品 feature，不變更 SPEC 版本。
完成條件：七份 TOML 可解析且與對應角色 Markdown 一致；五個 SKILL.md 通過格式驗證；
文件中的角色、路徑、指令可對應實際產物；保留產品 P1–P5、角色邊界、交接與獨立驗證要求。
客戶端重新載入與所有技能完整執行若未驗證須明示，不以靜態檢查代替產品驗收。

### DEV-CODEX 變更紀錄

- 2026-09-20：補 Codex 使用文件與五個專案 skills，修正既有角色的過時 MVP 指示；產品程式碼與契約未修改。
- 2026-09-21：QA 裁定 DONE；七角色解析／同步、五技能格式與設定稽核通過。新 session 載入、完整技能工作流與產品功能／安全未驗證，詳見驗收交接。

## 已完成產品任務

| ID | 任務 | 完成日 | 驗證者 |
|---|---|---|---|
| ZEN-006A | Console 早期切片：Run 觀測頁 + 發起 Run（ZEN-006 的一小片，使用者授權提前） | 2026-09-22 | @qa-verifier |
