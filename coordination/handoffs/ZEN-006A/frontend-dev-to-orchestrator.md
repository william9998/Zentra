# 交接單 Handoff

**任務**：`ZEN-006A` Console 早期切片：Runs 空狀態頁 + Run 觀測頁（原生 HTML/CSS/ES modules）
**從**：@frontend-dev　**給**：@orchestrator（下一棒建議 @qa-verifier）
**日期**：2026-09-21
**Spec 版本**：`SPEC v0.2.2` / `ui-contract v0.4.0` / `api-contract v0.2.2`

## 1. 我做了什麼

交付 `apps/console/` 的第一版：`runs.html`（永遠 Empty + 發起測試 Run 的原生 `<dialog>` + 本機暫存清單）
與 `run.html`（Run 觀測頁：狀態指示器、基本資料、事件時間軸、結果、用量），外加共用外框、
設計 token、圖示 sprite 與 `api.js` 契約層。**零建置、零執行期函式庫、沒有 `package.json`。**
只用後端真的存在的三個端點；不存在的端點（列表 / cancel / agents / 版本）**一行都沒呼叫**。

實作過程中在自己的程式碼抓到兩個會實際害人的 bug 並修掉（見 §6）：
`EventSource` 自身的 `error` 事件與契約的 `error` 事件型別撞名、以及 `[hidden]` 被 `.btn-*` 的
`display` 蓋掉。兩者都是用瀏覽器實測才看得到，不是讀程式碼能發現的。

## 2. 產出的檔案

| 檔案 | 說明 |
|---|---|
| `apps/console/runs.html` | `/console/runs`。只載入 CSS + `<main id="app">` + 頁面 module |
| `apps/console/run.html` | `/console/runs/{run_id}`。同上 |
| `apps/console/static/css/tokens.css` | ui-contract §1 **原封不動**（含刻意保留但不使用的 `--status-waiting`） |
| `apps/console/static/css/base.css` | reset、`.btn-*`、`.icon`、§2 狀態圓點、骨架屏、focus ring、`prefers-reduced-motion` |
| `apps/console/static/css/layout.css` | shell、卡片、事件時間軸、Empty/Error、`<dialog>`、暫存清單 |
| `apps/console/static/icons.svg` | §3 的 sprite。Lucide 線性、`stroke-width="1.5"`、只有表列的 10 個 symbol，**零 emoji** |
| `apps/console/static/js/api.js` | **契約唯一出口**。`RUN_STATUS` / `EVENT_TYPE` / `ERROR_CODE` 枚舉、`ApiError`、`getRun`、`createBuiltinRun`、`subscribeRunEvents`、snake_case → camelCase 正規化 |
| `apps/console/static/js/store.js` | ui-contract §6.1 的 37 行發布／訂閱，只有 run.html 用 |
| `apps/console/static/js/shell.js` | §5.1 共用外框：側欄（可收合，狀態存 localStorage）、麵包屑、`location.pathname` 判斷選中 |
| `apps/console/static/js/dom.js` | **§6.1 清單外的新增模組**（見 §8 Q3）：安全建節點、sprite 圖示、計時器登記簿、複製、時間格式化 |
| `apps/console/static/js/components/run-timeline.js` | §5.3：只追加、預設收合（原生 `<details>`）、payload + Copy、>2KB 再折一層、只渲染最近 200 筆 + 載入更早、>2000 筆提示 |
| `apps/console/static/js/pages/run.js` | Run 觀測頁：GET 初始狀態 → SSE → 終態重新 GET；RUNNING 顯示節點 + 每秒更新的耗時；五態齊備 |
| `apps/console/static/js/pages/runs.js` | Runs 頁：Empty + `<dialog>` 建立 Run + **ZEN-006A 臨時鷹架**（localStorage 暫存，檔頂已標註 ZEN-004 後刪除） |

**沒有**新增 `package.json` / `node_modules` / 任何 CDN `<script>`。我只寫了 `apps/console/**`，
沒有碰 `apps/api/**`、`docker/**`、`specs/**`。

## 3. 契約影響

- [x] 沒有影響對外契約（沒改任何 `specs/**`）
- 用到的端點只有三條：`POST /v1/agents/{agent_id}/runs`、`GET /v1/runs/{run_id}`、
  `GET /v1/runs/{run_id}/events`
- 與 @backend-dev 的 `apps/api/static.py` 對過：頁面檔在 `apps/console/` 根層、靜態資產在
  `apps/console/static/**`、頁面一律用 `/console/static/...` 絕對路徑 —— **三點都相符**

### 給後端注意的一個行為
SSE 的 `run.status` 只帶 `status`，拿不到 `output` / `error` / `usage` / `started_at`，
所以 **每收到一次 `run.status`，前端會回頭打一次 `GET /v1/runs/{run_id}`**（一個 Run 約 3–4 次）。
這是目前契約下唯一正確的做法，不是 bug；如果後端覺得這個量不可接受，請走 SPEC-QUESTION 讓
`run.status` 帶更多欄位，我再改。

## 4. 下一個人要做什麼

1. **@orchestrator**：把 ZEN-006A 的前端部分推進到 QA；BOARD 上補一筆「`runs.html` 的 localStorage
   暫存是臨時鷹架，ZEN-004 上線時要整段刪除」的技術債。
2. **@qa-verifier**：用真的 compose 環境（不是我的 stub）跑一次主流程：
   `/console/runs` → 發起測試 Run → 導到 `/console/runs/{run_id}` → 看到 `QUEUED → … → SUCCEEDED`
   與 `{"echo": ...}`。重點驗 §7 標「未驗證」的那些格子。
3. **@architect**：§8 的 SPEC-QUESTION 候選（尤其 Q1 的 `--dot-size` 與 Q4 的欄位名標籤）。
4. **ZEN-006 接手的人**：`api.js` 的 `BUILTIN_AGENT_ID` 與 `createBuiltinRun()` 是 fixture 專用，
   有真的 Agent API 之後換成通用的 `createRun(agentId, …)`；`runs.js` 的鷹架整段刪。

## 5. 我沒做的事（誠實列出）

指派單明確列為「不做」，我一個都沒偷做：

| 項目 | 原因 |
|---|---|
| `streaming-output.js`（逐字串流、游標、rAF 60ms 節流） | 目前沒有 `token` 事件，等 ZEN-002。`run-timeline.js` 能顯示 token 事件的摘要，但**沒有**逐字累積渲染 |
| 停止／取消按鈕 | `POST /v1/runs/{id}/cancel` 不存在，不放會 404 的按鈕 |
| 重試按鈕（`retryable` 時） | 同上，沒有端點。FAILED 時只顯示「這個錯誤標記為可重試」+ 文字指引 |
| Run 列表、篩選、cursor 分頁 | `GET /v1/runs` 不存在 |
| `version-upload.js`、`endpoints-card.js` | 等 ZEN-006 |
| `agents.html`、`agent.html`、`version-new.html` | 等 ZEN-006（側欄的 Agents 因此做成 disabled，不是會 404 的連結） |
| `Last-Event-ID` 續傳的前端配合 | 後端現在忽略它；`EventSource` 原生會帶，我沒有自己再寫一套重連 |
| 前端測試檔 | `tests/**` 是 @qa-verifier 的區域。我的驗證腳本只活在 scratchpad，**沒有進 repo** |

## 6. 已知限制與風險

| 項目 | 影響 | 建議 |
|---|---|---|
| **（已修）`EventSource` 的 `error` 事件與契約的 `error` 事件型別撞名** | 修之前每次斷線重連都會在時間軸多長出一列空的 `error`。已在 `api.js` 用「真正的 SSE 訊息是 `MessageEvent`（有字串 `data`）」擋掉 | 任何人之後寫 SSE 客戶端都會踩到，值得記進 ZEN-004 的注意事項 |
| **（已修）`[hidden]` 被 `.btn-*` 的 `display` 蓋掉** | 修之前「載入更早的事件」在只有 4 筆事件時也會顯示。已加 `[hidden] { display: none !important; }` | — |
| 終態後前端主動 `close()`，**依賴後端「終態事件 `terminal=true`」這個不變式** | 若某個 Run 進了終態卻沒有 terminal 事件，串流會一直掛著（只有 keepalive），連線狀態列會停在「接收事件中」 | ZEN-004 做 `Last-Event-ID` 時一併確認這個不變式；前端這邊沒有加保險絲（加了反而可能提前切斷重播） |
| 每個 `run.status` 觸發一次 `GET /v1/runs/{id}` | 一個 Run 多 3–4 個 GET | 見 §3 |
| RUNNING 但還沒收到任何 `node.*` 時，節點欄顯示「尚未進入節點」 | §2 要求 RUNNING 必須顯示節點名稱，但那個資訊此時真的還不存在 | 若 UI 設計師覺得措辭不妥可換字串 |
| `started_at` 為 `null` 時耗時改由 `created_at` 起算 | 排隊時間會被算進「已耗時」 | 終態後改用 `started_at → finished_at`，數字會校正 |
| 字型 `Inter` / `Outfit` / `Noto Sans TC` **沒有載入**（不引 CDN、不打包字型檔） | 實際會 fallback 到系統字型（macOS 上是 -apple-system） | 見 §8 Q6 |
| `.status-dot` 的 6px 借用 `--radius-sm` | 語意不漂亮，但避免寫死字面值 | 見 §8 Q1 |
| `@media (max-width: 768px)` 是全 CSS 唯一的字面值尺寸 | CSS custom properties 不能用在 media query 條件裡，無建置步驟也沒得預處理 | 見 §8 Q5 |
| 手機版只做到「側欄隱藏 + 內距縮小」 | 沒有漢堡選單，小螢幕上無法切換頁面（只能靠網址） | 內部工具，MVP 可接受；要改請開任務 |

## 7. 驗證狀態

> **我沒有啟動 compose、沒有連過真的 `apps/api`。** 以下全部是對 scratchpad 裡一個
> 「照著後端現況寫的假伺服器」＋ headless Chrome 152 做的。驗證腳本沒有進 repo。

| 檢查 | 結果 | 證據 |
|---|---|---|
| ES module 語法檢查（7 個 `.js`） | ✅ | `vm.SourceTextModule` 逐檔 parse，7/7 PASS（註：`node --check` 對含 `import` 的檔會**假通過**，不可用） |
| 主流程互動（Runs → dialog → 建立 → 觀測頁 → 回列表看暫存） | ✅ 23/23 | CDP 驅動 Chrome：Empty 文案、`showModal`、焦點落在輸入框、422 顯示 `VALIDATION_ERROR` + details、成功導向 `/console/runs/{run_id}`、8 筆事件無重複、預設全收合、展開看 payload、暫存清單、`localStorage` 只有 `runId/question/createdAt` |
| **XSS：使用者輸入不會變成節點** | ✅ | 送出 `來自 CDP 的測試 <img src=x onerror=alert(1)>`，輸出區與暫存清單皆以純文字呈現，DOM 內 `img` 數 = 0 |
| **終態後不重連、不重播**（本切片最大風險） | ✅ | `requests.log`：每個 Run 的 `/v1/runs/{id}/events` **只有 1 次**（succ / big / many / live 皆是） |
| **斷線重連 + `id`(seq) 去重** | ✅ | 伺服器第一次連線只送 3 筆就斷、第二次從 seq 0 重播 5 筆 → 畫面剛好 5 列且順序正確；過程中出現過「重新連線中」 |
| RUNNING：節點名稱 + 每秒更新的耗時 | ✅ | `執行中 · echo_node · …s`，3 秒後數字變大 |
| Loading 三態骨架屏（GET 慢 2.5s） | ✅ | 22 個 `.skeleton` + 「正在載入這個 Run 的事件…」，載入完歸零 |
| Empty | ✅ | runs 頁（列表 + 暫存皆空）、run 頁（結果／用量未產生時） |
| Error | ✅ | `GET /v1/runs/missing-1` → 404：頁面錯誤區顯示 `NOT_FOUND` + message + `run_id` + 指引；FAILED Run 顯示 `SANDBOX_TIMEOUT` + 可重試標記 + `run_id` + 錯誤細節 |
| 200 筆視窗 / 載入更早 / >2000 筆提示 | ✅ | 灌 2101 筆：渲染 200 → 點一次變 400 → 提示文字出現 |
| 大 payload（>2KB）折疊 | ✅ | 4KB payload 顯示「展開完整 payload（4.0 KB）」 |
| 原生元素行為（`<details>` 點擊展開、`<dialog>` ESC / 取消關閉） | ✅ | 皆通過 |
| 側欄收合 + localStorage 跨頁保持 | ✅ | 收合後寬度 `0px`，換頁仍收合 |
| CSS token 有生效、focus ring | ✅ | `body` 背景 `rgb(13,13,13)` = `--bg-base`；圓點 `rgb(74,144,112)` `6px` = `--status-succeeded`；**真的按 Tab** 後 outline 為 `2px solid rgb(176,176,176)` = `--border-focus` |
| 無未捕捉例外 / 無 `console.error` | ✅ | 全程監聽 `Runtime.exceptionThrown` 與 `consoleAPICalled(error)`，皆為 0 |
| 視覺檢查 | ✅（Chrome 截圖） | 深色單色、髮絲邊框、sprite 圖示正常、mono 用在 ID/payload/數值 |
| **對真實 `apps/api` / compose 的端到端** | ❌ **未驗證** | 我沒有啟動服務，也沒有重建映像 |
| **Safari / Firefox** | ❌ **未驗證** | 只有 Chrome 152。外部 SVG sprite `<use href="file.svg#id">` 在各家都該支援，但我沒實測 |
| **剪貼簿真的複製成功** | ❌ **未驗證** | headless 沒有剪貼簿權限，走到 `execCommand` 退路也失敗，按鈕顯示「複製失敗」——**退路與回饋路徑有驗到，實際寫入沒有**。請 QA 在真瀏覽器點一次 |
| `prefers-reduced-motion` 的實際表現 | ❌ 未驗證 | CSS 寫了，沒有開系統設定實測 |
| 螢幕報讀器 / 完整鍵盤巡覽 | ❌ 未驗證 | 只驗了單次 Tab 的 focus ring 與原生元素行為；沒有用 VoiceOver 跑過 |
| 手機／窄螢幕版面 | ❌ 未驗證 | 只有 1440×1000 |
| `CANCELLED` 狀態的畫面 | ❌ 未驗證 | 後端目前產不出這個狀態（沒有 cancel 端點），只走過程式碼路徑、沒有實際渲染過 |
| `token` / `log` / `custom` / `error` 事件在**真實**資料下的樣子 | ⚠️ 只用假事件驗過 | ZEN-000 不會產生這四種；我用 stub 灌進去確認不會崩、各有一行摘要 |

## 8. 未決的 SPEC-QUESTION（候選，請 @architect 裁決後我再改）

- **Q1**：§2 要求狀態圓點 6px，但 §1 沒有 6px 的尺寸 token。我暫時引用 `--radius-sm`（值剛好 6px）。
  建議 §1 新增 `--dot-size: 6px`。
- **Q2**：§4 規定 Empty「一行說明 + **一個 secondary 按鈕**」。事件時間軸在 Run 進行中的 Empty
  其實沒有合理動作，我放了「重新整理」。要不要允許子區塊的 Empty 省略按鈕？
- **Q3**：§6.1 的檔案清單沒有共用工具模組，我新增了 `static/js/dom.js`（安全建節點 / 圖示 /
  計時器登記 / 複製 / 時間格式化）。請確認保留，或指定併進哪個既有檔。
- **Q4**：§6.2 說契約欄位名只准出現在 `api.js`。我遵守了**屬性存取**這一點（已 grep 驗證），
  但畫面上把 `run_id` / `agent_id` / `llm_calls` / `cost_usd` 當**標籤文字**顯示給工程師看。
  請確認「標籤文字」不算違規（我認為這對除錯有價值）。
- **Q5**：硬性規則 5「不准寫死間距」與 media query 衝突 —— CSS 變數不能用在 media query 條件裡，
  沒有建置步驟也無法預處理。目前全 CSS 只有 `768px` 這一個字面值斷點，請確認可接受。
- **Q6**：token 指定 `Inter` / `Outfit` / `Noto Sans TC`，但我們不引 CDN（ADR-0006 精神）也沒有字型檔。
  要自帶字型檔（多幾百 KB 靜態資產）還是接受系統 fallback？目前是 fallback。

---

## 9. 整合驗證後修正（2026-09-21，接 @orchestrator 的真實環境回報）

### 缺陷
Run 觀測頁「基本資料」卡片裡 `run_id` 旁的複製按鈕，標籤「複製」被擠成直排一字一行。
@orchestrator 在真實 compose + headless Chrome（viewport 1440）量到 `w: 13, h: 36`。

### 重現
我原本的 stub 用的是短 id（`succ-1`），所以**漏掉了這個缺陷** —— 改用 UUID 長度的
`succ-0193a7c0-0000-7000-8000-00000000abcd` 後在本地完整重現：`labelW: 12, labelH: 36`。
**教訓：fixture 的資料長度要貼近真實，短 id 會蓋掉版面問題。**

### 成因
`.btn-*` 當 flex item 時可以被壓縮（預設 `flex-shrink: 1`）。`.inline-row` 裡的 `run_id`
是長字串（`overflow-wrap: anywhere`），flex 收縮時按鈕跟著被壓到比內容還窄
（按鈕 60px，扣掉內距 24 + 圖示 14 + gap 8 只剩 14px 給標籤），兩個中文字因此各佔一行。
不是高度或字級的問題，是**按鈕被允許收縮**。

### 修正（只有 CSS，沒有新增任何字面值尺寸）
| 檔案 | 變更 |
|---|---|
| `apps/console/static/css/base.css` | `.btn-primary/.btn-secondary/.btn-ghost/.btn-danger` 加 `flex: none;` 與 `white-space: nowrap;`（兩者都是行為約束，不是尺寸） |
| `apps/console/static/css/layout.css` | 新增 `.inline-row > .meta-value { min-width: 0; flex: 1 1 auto; }` —— 會換行的值先讓位給固定寬度的動作按鈕 |

### 量測（標籤 `<span>` 的 w×h，我的 stub + headless Chrome）
| viewport | 修正前 meta `複製` | 修正後 meta `複製` | 結果卡片 `複製輸出` | 時間軸 `Copy` |
|---|---|---|---|---|
| 1440 | **12 × 36**（兩行） | **24 × 18**（一行） | 48 × 18（前後一致） | 29 × 18（前後一致） |
| 1024 | 18 × 36（兩行） | 24 × 18 | 48 × 18 | 29 × 18 |
| 820 | 24 × 18 | 24 × 18 | 48 × 18 | 29 × 18 |
| 768 | — | 24 × 18 | 48 × 18 | 29 × 18 |
| 480 | — | 24 × 18 | 48 × 18 | 29 × 18 |

另外兩顆複製按鈕（結果卡片、時間軸展開後的 payload）**本來就正常，修正後數值完全沒變**。

### 回歸（確認 `flex: none` 沒有波及其他按鈕）
| 檢查 | 結果 |
|---|---|
| 主流程互動 23 項 | ✅ 23/23 |
| 時間軸 200 筆視窗 / 載入更早 / >2000 筆提示 | ✅ 全通過 |
| 側欄收合、`<details>` 點擊、`<dialog>` ESC/取消 | ✅ 全通過 |
| 「載入更早的事件」仍為整列寬 | ✅ `1098 × 28`（單行） |
| Empty 的「發起測試 Run」 | ✅ `114 × 32` |
| dialog 的「取消 / 建立 Run」 | ✅ `60 × 32` / `88 × 32` |
| 視覺 | ✅ 截圖確認 `run_id` 換成兩行、按鈕在旁邊單行呈現 |

### 沒改的事
- `llm_calls` 等欄位標籤在 `innerText` 回大寫是 `.meta-label { text-transform: uppercase }`
  的設計意圖（DOM 的 `textContent` 仍是小寫），**不是缺陷，我沒有動它**。
- 沒有碰 `apps/api/**`、`docker/**`、其他畫面，也沒有新增任何範圍外的東西。

### 這次驗證的限制
量測是在我自己的 scratchpad stub（讀的是同一份 `apps/console/**` 檔案）+ headless Chrome 152 上做的，
**沒有對跑著的容器驗**（容器裡是重建前的舊檔）。請 @orchestrator 重建映像後再量一次那顆按鈕。
