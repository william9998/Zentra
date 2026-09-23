# 任務看板 (Task Board)

> **唯一的任務真相。** 所有 agent 開工前看這裡，收工後更新這裡。
> 狀態機：`BACKLOG → SPEC_READY → IN_PROGRESS → REVIEW → QA → DONE`（任何階段可轉 `BLOCKED`）
> 規則：只有 `@architect` 能推進到 `SPEC_READY`；只有 `@qa-verifier` 能推進到 `DONE`。
>
> **對齊 SPEC `v0.3.1`。** OCI image 發布依 ADR-0008 取代尚未實作的 bundle/venv 路徑；
> 舊編號 ZEN-001～ZEN-011 的歷史對照見 §已作廢。

## 八週路線圖

一條主線，八週。**每一週都要有東西能跑**，不是八週後一次整合。

| 週 | 里程碑 | 任務 |
|---|---|---|
| W1–2 | 一條 curl 打得通的路徑 | ZEN-000 |
| W2–3 | 註冊自己的 OCI image 就能跑 | ZEN-008 |
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
| **ZEN-008** | **OCI image 型 Agent**：digest 註冊 → pull/validate → Registry → Console/REST/MCP → 一 Run 一盒 | `IN_PROGRESS` | @backend-dev + @platform-eng + @frontend-dev；@qa-verifier 驗收 | ZEN-000；整合 ZEN-003/004/005/006 | [spec](../specs/features/ZEN-008/spec.md) · [acceptance](../specs/features/ZEN-008/acceptance.md) · [ADR](../specs/adr/ADR-0008-oci-image-agent-packaging.md) |

> **ZEN-009 的由來（2026-09-22，使用者裁示）**：使用者要求「在前端上傳 docker image 建立 Agent」。
> [ADR-0008](../specs/adr/ADR-0008-oci-image-agent-packaging.md) 當初評估過並否決了兩個選項：
> **A（API 直接收 `docker save` tar）** 與 **B（Zentra 自帶 Registry）**。使用者選擇的是**兩者的組合**：
> 上傳 UX 來自 A，架構來自 B —— 收 tar 後由平台 push 進內部 Registry，再走既有的 digest 流程。
>
> **決定性的實測（主 session，2026-09-22）**：`docker save` → `docker load` 之後
> `RepoDigests` 變成 `[]`，registry 的 manifest digest（`sha256:5f177b80485c…`）消失，
> 只剩 config digest（`sha256:a6040472a3b0…`），兩者**不是同一個識別**。tar 大小 216 MB。
> 因此「直接收 tar 並以本機 image 執行」會讓 `PUBLISHED` 版本**失去可重新 pull 的來源**，
> 一次 `docker image prune` 就永久不可執行 —— 這砸掉 P5「版本不可變且可重現」。
> 選 B 的架構正是為了避開這點：digest 語意完全不變。
>
> **代價**：長駐服務 4 → 5，違反 [ADR-0005](../specs/adr/ADR-0005-mvp-single-host-scope.md) 的單機降維前提之一。
> ADR-0008 否決 B 的理由是「TLS、認證、GC、容量、備份」，但那是假設做成對外通用 Registry；
> 只掛 `zentra-net`、不對外開 port、不做認證的內部 Registry 成本較低。**這個取捨要由 @architect 在 ADR-0009 正式裁決，不是這段註記說了算。**
>
> **ADR-0009 已落地（2026-09-22），ZEN-009 進 `SPEC_READY`。**
>
> **第二版裁決（2026-09-23）**：使用者改選「允許同機 `docker push` 到內部 Registry」，理由是
> AWS（ECR）與 Azure（ACR）都是 registry-based、**沒有一個收 `docker save` tar 上傳**。
> 架構師據此把 **tar 上傳整條撤回（不是延後）**，ZEN-009 縮成「compose 加一個服務 + 文件」。
>
> **架構師標為未實測的關鍵推論「後端增量是零行程式碼」，已由主 session 實測確認（2026-09-23）**：
> `docker tag` → `docker push localhost:5010/my-own-agent:2.0.0` → 用**既有的** JSON
> `POST /v1/agents/{id}/versions` 填 `localhost:5010/my-own-agent@sha256:5f177b80485c…`
> → `BUILDING` → `PUBLISHED`（`runtime_contract: zentra.image/v1`，entrypoints 抽出成功）
> → `runs:invoke` → `SUCCEEDED`。**全程未改任何一行後端程式碼。**
> 因此裁決 2 的成本比較成立：直接 push 路徑的增量確實是零。
> 這不取代 QA 驗收——registry 進 compose 後（`localhost:5000`）仍須重驗一次。
>
> **ADR-0009 的關鍵假設已由主 session 實測確認**（架構師在交接單 §誠實回報 標為未實測）：
> `save → load` 後 `RepoDigests` 為 `[]`；`tag → push` 進內部 Registry 後
> `RepoDigests` 被填回 `localhost:5010/uploaded/demo@sha256:5f177b80485c…`，
> **且 digest 與原始 registry push 的完全相同**（manifest 由同一組 layer/config 決定性算出）。
> 接著刪光本機 image（`docker images -q` 歸零）後，**只憑 digest 重新 pull 成功**，
> ImageID 與契約 label 皆相同。ZEN-009 的可重現性前提成立。
> 這不取代 QA 的 §7.1 驗收——QA 仍須在實作完成後以自己的證據驗一次。

> **ZEN-008 現況（2026-09-22，主 session 接手 Codex 未完成的實作）**：
>
> **追加缺陷（2026-09-23，主 session 實測）：Docker Hub 的 image 目前完全無法註冊。**
> `apps/sandbox/drivers/docker_driver.py:181` 以**字面字串**比對註冊的 `image_ref` 與 Docker 的 `RepoDigests`，
> 但 Docker 會把 `docker.io/` 前綴正規化掉：
> 註冊 `docker.io/library/alpine@sha256:d9e853…` → pull **成功** → `RepoDigests` 回報
> `["alpine@sha256:d9e853…"]` → 比對不相等 → `BUILD_FAILED`。
> 改填短格式 `alpine@sha256:…` 則在 schema 驗證就被擋（`image_ref must contain a canonical lowercase repository`）。
> 兩條路都死，**Docker Hub（含使用者命名空間 `myuser/*`）一個都註冊不了**；
> GHCR 與自建 registry 不受影響，因為不會被正規化。
> 修法方向：比對前把兩邊正規化（`docker.io/library/x` ≡ `docker.io/x` ≡ `x`），而不是字面比對。
> 屬 `apps/sandbox/**`（@platform-eng）。**這直接擋住最常見的使用情境，建議優先於 ZEN-009。**
> Codex 完成實作但**在後端交件前用盡額度**，因此沒有 backend-dev 交接單。主 session 接手後：
>
> **修正三個真缺陷**（都擋住系統啟動或 migration）：
> 1. `apps/api/services/runs.py` 的 `BUILTIN_MANIFEST` 引用已移除的 `BUILTIN_AGENT_SEMVER` → API 無法 import。該常數在 ZEN-008 後為死碼（manifest 改由 migration seed 進 DB），已刪除。
> 2. `migrations/versions/0004`：seed 以 `bindparams` 傳 `str` 進 uuid 欄位 → 改用 `CAST(:id AS uuid)`（3 處）。
> 3. 同上：SQLAlchemy `text()` 把 JSON 字面值的 `"builtin":true` 冒號解析成綁定參數 → 改用綁定參數傳 JSON。
>
> **補上 MCP 門面**（原本完全沒實作，`fastmcp` 宣告了依賴但零使用，`endpoints.mcp` 只是字串）：
> 新增 `apps/api/mcp.py`，`/mcp/agents/{agent_id}` Streamable HTTP。實測 `tools/list` 的 `inputSchema`
> 與 entrypoint 的 `input_schema` 逐欄位相同、`tools/call` 同步約 1.8 秒、`_async: true` 回 `run_id`、
> DB 記錄 `trigger=MCP`、`/openapi.json` 無洩漏。P3 同源是結構性的：兩個門面共用
> `services.agents.mcp_tools()` 與 `services.runs.create_run`，沒有第二條可漂移的實作路徑。
>
> **測試現況**：`tests/zen008/` 由 @qa-verifier 寫出 10 個檔案 → **66 passed / 2 failed / 137 skipped**。
> `tests/zen000` + `tests/zen006a` 經同一輪更新後 **157 passed / 10 skipped / 2 xfailed**（過期的 L4 斷言與被 ZEN-008 撐破的範圍護欄已更新，非刪除）。
> 137 個 skipped 全部等同一個前置條件：本機測試 registry（`docker run -d --name zentra-qa-registry -p 5010:5000 registry:2`），
> 那是 OCI pull/digest 主路徑，**未驗＝核心未驗**。
>
> **QA 發現的 2 個真缺陷（未修，屬 `apps/api/**`）**：
> - `GET /v1/agents` 回應缺 `next_cursor`，違反 api-contract §0 的分頁約定。
> - `GET /v1/agents/{id}` 在值為 null 時直接省略 `description` 與 `latest_published_version` 欄位，而非保留欄位給 `null`。
>
> **其他已知缺口**：migration 0004 未處理孤兒 runs（既有 runs 指向不存在 agent 時，加 FK 會直接失敗；
> dev DB 當時是經使用者授權刪除一列測試殘骸才通過）；無 backend-dev 交接單。
>
> **@qa-verifier 的驗收在寫裁定前被使用者中止，因此沒有裁定。維持 `IN_PROGRESS`，不得視為通過。**
| ZEN-002 | **SDK v1 + Internal Gateway**：`ctx.llm` / `ctx.logger` / `ctx.emit`，OpenAI 相容 LLM 代理（P4） | `BACKLOG` | @backend-dev | ZEN-008 | — |
| ZEN-003 | **沙盒四層隔離硬化**：internal 網段、cap_drop、cgroups、唯讀 + `capabilities()` | `BACKLOG` | @platform-eng | ZEN-000 | — |
| ZEN-004 | **Run API 完整版**：cancel、列表分頁、SSE `Last-Event-ID` 續傳、逾時、冪等 | `BACKLOG` | @backend-dev | ZEN-000 | — |
| ZEN-005 | **Interface Generator**：一份 entrypoints → OpenAPI + MCP tool schema（P3） | `BACKLOG` | @backend-dev | ZEN-008 | — |
| ZEN-006 | **Console 五個畫面**（原生 HTML/CSS/ES modules，零建置）：Agents 列表 / Agent 詳情 / 註冊新 image 版本 / Runs 列表 / Run 觀測 | `BACKLOG` | @ui-designer → @frontend-dev | ZEN-004 + ZEN-008 | — |
| **ZEN-009** | **內部 Registry**：compose 加 `registry:2`（`127.0.0.1:5000`），使用者**同機** `docker push` → 用既有端點註冊 digest。**tar 上傳已撤回** | `SPEC_READY`（第二版） | @platform-eng（主體）；@qa-verifier 驗收 | ZEN-008 | [ADR-0009](../specs/adr/ADR-0009-internal-registry-image-upload.md) · [spec](../specs/features/ZEN-009/spec.md) · [交接](handoffs/ZEN-009/architect-to-implementation.md) |
| ZEN-007 | **收尾與驗收**：隔離回歸測試、失敗路徑、`zentra` CLI、demo 腳本、README | `BACKLOG` | @qa-verifier + 全員 | ZEN-002…006 + ZEN-008 | — |
>
> ---
>
> **🔴 隔離缺陷 `ZEN008-ISO-01`（2026-09-23 實測證實，擋 ZEN-008 `DONE`）**
>
> 使用者 image 的 Dockerfile 若宣告 `VOLUME`，Docker daemon 會把它**合併成可寫的匿名 volume**，
> 平台設定的 `read_only: True` 擋不住。實測 `tests/zen008/test_image_volume_escape.py`：
>
> ```
> volume_mounts: [{"Destination": "/escape", "RW": true,
>                  "Source": "/var/lib/docker/volumes/940efc7ea3ad…/_data"}]
> sandbox_write: "WROTE"
> 沙盒銷毀後：volume 仍在宿主機，沙盒寫的 marker c973c99f79704f83995dd009621d1330 仍可讀
> ```
>
> **後果**：使用者提供的 image 能在宿主機留下跨 Run 存活、平台不知道也不會回收的資料，
> 打破「一次性沙盒、結束即銷毀」。同時 `test_sandbox_security.py::test_workspace_is_the_only_writable_mount` 失敗
> （可寫掛載不只 `/workspace` tmpfs）。
>
> **這是 @platform-eng 在 ZEN-008 交接單自己預測過的風險**（原文：「若 SDK/daemon 把 image VOLUME 合併回來，
> 可能多出 writable volume；Z8-23 必須 inspect Mounts；**失敗不得放行**，需改用能明確送空 `Config.Volumes` 的低階 API」）。
> 現在證實失敗，**依其自訂條件不得放行**。
>
> 修法方向（其本人提出）：改用能明確送空 `Config.Volumes` 的低階 API，不要依賴 SDK 的預設合併行為。
> 屬 `apps/sandbox/**`（@platform-eng）。
>
> **為什麼現在才發現**：這幾條測試先前卡在 `require_registry()` 而被 skip。
> 主 session 手動起了測試 registry 後才執行到——**skipped 不等於通過**，這是實例。

> **ZEN-008 架構裁決（2026-09-22）**：使用者明確要求 Docker image 型 Agent。
> ADR-0008 選擇「既有 OCI Registry + canonical digest reference」，不收 image tar、不在 compose 加 Registry；
> PostgreSQL 只存 metadata，q.build pull/validate 後才 PUBLISHED。由於 ZEN-001 bundle 路徑尚未實作，
> OCI image 直接取代它，避免雙軌。SPEC/API v0.3.1、UI contract v0.5.1 與開發前 acceptance
> 已由各自 owner 完成並經 architect 一致性檢查；Q-008 已解決。**2026-09-22 裁定進入 `SPEC_READY`。**
>
> **ZEN-008 trigger 裁決（2026-09-22）**：QA 發現 Console 與外部 REST 共用同一 POST，卻要求
> `trigger=CONSOLE/API`，在無認證 MVP 無法可信區分。SPEC/API v0.3.1 將 trigger 定義為 server-known ingress：
> REST（含 Console）=`API`、FastMCP=`MCP`；body/header 不得改寫，`CONSOLE` 自枚舉移除。

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

> **Agent metadata 編輯／刪除（2026-09-22，使用者直接授權，主 session 實作）**：
> Agent 詳情頁頁首新增「編輯」與「刪除」，接的是 api-contract §2 早就定義、後端也早就實作的
> `PATCH /v1/agents/{id}` 與 `DELETE /v1/agents/{id}`（軟刪除）。**沒有新端點、沒有新畫面、沒有新 route。**
> 新增 `apps/console/static/js/components/edit-agent-dialog.js`、`delete-agent-dialog.js`
> 與 `layout.css` 的 `.page-actions`；`api.js` 補 `updateAgent()` / `deleteAgent()`。
> PATCH 只送真的改動的欄位，兩欄都沒改時 primary CTA 維持 disabled（空 body 後端回 422）。
>
> **這是對 ui-contract v0.5.1 的偏離，待 @ui-designer 追認 —— 見 Q-009，不得視為已核准。**
> §5 沒有這兩個元件的五態規格，§5.12 的流程也沒有這條路徑。
> `tests/zen006a/test_console_source_contract.py` 的精確檔案集合護欄已跟著放寬兩個檔名，
> 護欄本身（白名單制、端點上限、innerHTML 禁令）沒有放鬆。
>
> **證據**：`tests/zen006a` 在容器內對活著的服務 **93 passed / 1 xfailed**（含 4 條靜態護欄與路由測試）；
> 另以 headless Chrome 實測完整點擊流程 **21/21**：預填現值、無變更時 disabled、儲存後標題與麵包屑就地更新、
> 描述含 `<img onerror>` 以 textContent 寫入（DOM 內 `img` 數 0）、撞名回 `VALIDATION_ERROR` 且 dialog 不關、
> 刪除後導回列表且詳情 API 回 404、0 未捕捉例外。該腳本是一次性驗證，未進 repo。
>
> **未驗證**：Safari／Firefox、窄螢幕、螢幕報讀器、鍵盤 focus trap 的完整行為。
> **另一個 CDP 驅動的自動化測試沒有補**（`tests/zen006a/cdp_console_e2e.mjs` 需要 node，本機沒有）。
>
> **順帶發現的後端缺陷 `AGENT-D1`（既有行為，非本次引入，未修）**：`agents.name` 是無條件的
> `UNIQUE` 約束，軟刪除只寫 `deleted_at`。因此**刪掉的 Agent 仍永久佔用名稱**，再建同名會回
> `422 VALIDATION_ERROR: agent name already exists`，而使用者在 Console 上完全看不到那個 Agent。
> 修法要 migration（改成 `deleted_at IS NULL` 的 partial unique index）＋ @architect 裁決刪除語意，
> 屬 `apps/api/**`，不在本次授權範圍內。

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
| Q-008 | ZEN-008 spec §6 只寫「import Agent `build()`」，沒寫簽章。實際 runtime 呼叫的是 `build(context)`，寫成 `def build()` 會 `TypeError` 且只在驗證沙盒裡才看得到。契約要不要明訂簽章？ | @orchestrator | RESOLVED | **RESOLVED**：契約明訂 `def build(ctx: ZentraContext) -> StateGraph`，只接受一個位置參數；`validate` 先檢查 arity，不符回 `BUILD_FAILED`。收斂進 SPEC v0.4.0 §8.1。 |
| Q-009 | §6 說「Agent 所有依賴都已在 image 內」，但沒列出 **SDK 自己**需要 `jsonschema`。少裝會在 import 階段就死。要在契約列出 SDK 的執行期依賴，還是讓 SDK 自己宣告（`packages/sdk` 目前沒有 `pyproject.toml`）？ | @orchestrator | RESOLVED | **RESOLVED**：由 SDK 自己宣告（新增 `packages/sdk/pyproject.toml`），image 契約不列舉傳遞依賴；runtime 啟動做 import 自檢。收斂進 SPEC v0.4.0 §8.1。 |
| Q-010 | §11 把「官方 base image 與範例 Dockerfile」列為後續文件，但沒有它使用者做不出能通過驗證的 image。`examples/demo-echo/` 已補上可運作的範例——要不要升級成正式的官方 base image？ | @orchestrator | RESOLVED | **RESOLVED**：升級為正式產出 `zentra/agent-base:1.0`（`docker/agent-base.Dockerfile`），本機 build、不發佈、不簽章、不多架構。工作排進 ZEN-009。 |
| Q-008 | ZEN-008 Console 與外部 REST 共用 `POST /v1/agents/{id}/runs`，如何可信產生不同 `trigger`？ | @qa-verifier | RESOLVED | `trigger` 表示接入協定：REST/Console=`API`、MCP=`MCP`。移除 `CONSOLE`；不接受 body/header discriminator（SPEC/API v0.3.1） |
| Q-009 | Agent 詳情頁已實作「編輯 metadata」與「刪除」兩個 dialog（`PATCH` / `DELETE /v1/agents/{id}`，api-contract 早有定義），但 ui-contract §5 沒有這兩個元件的規格與五態、§5.12 的操作流程也沒有這條路徑。請 @ui-designer 追認或指定改法。**已由使用者於 2026-09-22 直接授權先實作**，見下方偏離紀錄。 | @orchestrator | OPEN | — |

**提問格式**（新增時照抄）：
```
| Q-00X | <具體問題，附上為什麼卡住> | @<你> | OPEN | — |
```
提問後請同時把相關任務標為 `BLOCKED`，並繼續做不相依的部分。

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
