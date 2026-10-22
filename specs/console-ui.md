# Console 網頁規則

> 原生 HTML + CSS + ES modules，**零建置**（沒有 npm、沒有 bundler）。由 `app` 提供靜態檔，和 API 同源。
> 欄位與端點看 [api.md](api.md)。設計 token 的實際值以 `apps/console/static/css/tokens.css` 為準，這裡不重複列。

## 硬性規則（違反直接退回）

1. **不准用任何執行期函式庫或建置工具**：React / Vue / Alpine / htmx / jQuery / lodash / d3 / bundler / TypeScript / npm / `package.json` 全部不准。覺得需要 → 先問使用者。
2. **不准用 `innerHTML` 寫入任何來自 API 或使用者的字串**（原生 JS 最容易出的 XSS）。
   樣板字串只能用在「固定的結構」，動態內容一律用 `textContent` / `append`。
   LLM 的輸出、節點名稱、錯誤訊息，全都是不可信的字串。
3. **不准寫前端路由器。** 網址由 `app` 的路由給出，頁面從 `location.pathname` 讀 id。
4. **不准寫死色碼、間距、字級、圓角。** 只能引用 `tokens.css` 的變數。
5. **每個 `mount()` 都要有 `destroy()`**，而且要真的關掉 `EventSource`、`setInterval`、`requestAnimationFrame`。
6. **`api.js` 是契約的唯一出口**：API 的欄位名只准出現在 `api.js`，頁面和元件只拿它回傳的 view model。
7. HTML 裡不准有內嵌 `<script>`。
8. 不准把任何憑證放進 `localStorage`（偏好設定可以）。

**第 2 條的寫法對照：**

```js
// ❌ 錯：agent.name 來自 API，可以塞進 <img onerror=…>
card.innerHTML = '<span>' + agent.name + '</span>';

// ✅ 對：
const span = document.createElement('span');
span.textContent = agent.name;
card.append(span);
```

## 設計原則

深色為預設、近乎單色、克制。

| 原則 | 意思 |
|---|---|
| 單色優先 | 主色是高級灰。**顏色只用來傳達狀態**，不拿來裝飾 |
| 表面靠明度分層 | 用白色透明疊加來分層，不用陰影（modal / dropdown 除外） |
| 髮絲邊框 | 低對比的邊框 |
| 克制的動態 | 統一 `0.2s`；只有「進行中」的東西才動；尊重 `prefers-reduced-motion` |

**絕對禁止**：用 emoji 當圖示（一律用 `static/icons.svg` 的 Lucide 線性 SVG，`stroke-width 1.5`）、彩色漸層、光暈、彩色按鈕、自創尺寸、**只靠顏色表達狀態**（每個狀態都要有文字標籤）。

淺色模式：頂部列右側一顆只有圖示的切換鈕（深色時顯示 `i-sun`、淺色時顯示 `i-moon`，`aria-label` 寫完整句子），只改 `<html data-theme>`。
沒存過偏好就跟隨 `prefers-color-scheme`，偏好存在 `localStorage`。

## 狀態怎麼顯示

統一用「6px 圓點 + 文字標籤」，不用圖示，更不用 emoji。

| Run 狀態 | 標籤 | 動態 | 補充 |
|---|---|---|---|
| `QUEUED` | 排隊中 | 無 | |
| `SCHEDULING` | 配置中 | 脈動 2s | |
| `RUNNING` | 執行中 | 脈動 1.5s | 要同時顯示目前節點和已耗時 |
| `SUCCEEDED` | 已完成 | 無 | |
| `FAILED` | 失敗 | 無 | 要顯示 `error.code`；`retryable` 才給「重試」 |
| `CANCELLED` | 已取消 | 無 | 標籤用 disabled 色，要和 FAILED 明確區分 |

| 版本狀態 | 標籤 | 行為 |
|---|---|---|
| `BUILDING` | 驗證中 | 輪詢；超過 3 秒就顯示「平台正在拉取、檢查並驗證 image · 已耗時 Ns」。**不得猜目前在哪個 stage** |
| `PUBLISHED` | 已發布 | 顯示完整 digest、platform、size、runtime contract、entrypoints |
| `FAILED` | 驗證失敗 | 顯示 `BUILD_FAILED`、`error.message` 全文（可複製）、`details.stage` |

**不渲染 `WAITING_INPUT`**（已移除）。

## 每個元件都要有五種狀態

缺一個就不准交付：

| 狀態 | 規則 |
|---|---|
| Loading | 骨架屏；**超過 3 秒要說明在做什麼** |
| Empty | 一行說明「這裡會出現什麼」+ 一個次要動作。**不要只寫「無資料」** |
| Error | `error.code`（等寬字）+ 識別碼（`run_id` / `agent_version_id`）+「我現在該做什麼」。`retryable` 才給重試 |
| Partial / Streaming | 保留已知的內容，並標明進度 |
| Success | 正常狀態 |

## 元件的寫法

一個元件 = 一個 ES module，匯出 `mount(el, props)`：

```js
export function mount(el, props) {
  el.innerHTML = render(props);   // 固定結構的樣板，一次寫入；動態的值之後用 textContent 填
  bind(el, props);                // 事件委派綁在 el 上
  return { update(next) {…}, destroy() {…} };
}
```

沒有 diff、沒有響應式：狀態變了就**整塊重畫**。**唯一的例外是事件流（只追加，不重畫）。**

## 檔案與頁面

```
apps/console/
  agents.html  agent.html  version-new.html  mcp.html  runs.html  run.html     ← 每個只有 CSS、<main id="app">、一支 page module
  static/icons.svg  static/css/{tokens,base,layout}.css
  static/js/
    api.js     fetch 包裝 + 欄位正規化（契約唯一出口）
    dom.js     安全建節點、圖示、計時器登記、複製、時間格式化
    store.js   約 40 行的發布／訂閱
    shell.js   共用外框（側欄、麵包屑、主題切換）
    pages/     agents · agent · version-new · mcp · runs · run
    components/
```

| 網址（`app` 的 FileResponse 路由；`/` 會轉址到 `/console/agents`） | 頁面 |
|---|---|
| `/console/agents` | Agent 總覽（卡片格子）＋「建立 Agent」 |
| `/console/agents/{id}` | Agent 詳情 |
| `/console/agents/{id}/versions/new` | 註冊版本 |
| `/console/mcp` | MCP 接口：網址、發 MCP key、勾選這把 key 看得到的 Agent |
| `/console/runs` | 跨 Agent 的 Runs 列表與篩選 |
| `/console/runs/{run_id}` | Run 觀測 |

沒有萬用路由（catch-all），404 就該是 404。改了 `apps/console/` 之後要 `make rebuild-app` 才看得到。

**Agent 詳情頁的組成**（由上到下）：
metadata（含完整 `agent_id` + Copy，這是 Console 上唯一能複製 id 的地方）→ 版本 → 呼叫區 → endpoints → 最近 Runs，
再往下是 LLM 憑證、常駐容器、API key、用量、設定、機密、對外白名單與寄信、對外請求稽核等卡片。

**同一頁最多一個主要（primary）按鈕**：總覽頁是「建立 Agent」、註冊頁是「註冊並驗證」、詳情頁有可呼叫版本時是「執行 Agent」。

## MCP 頁長這樣

```
┌ MCP ───────────────────────────────────────────────────────┐
│ MCP   http://localhost:8000/mcp                    [Copy]  │  ← 接口網址
│ （對端平台填這個網址，並帶 Bearer key…）                       │
│                                                            │
│ 用途名稱  [ Dify 正式環境            ]                       │  ← 發新 key
│ 開放的 Agent                                                │
│   ☑ stock-agent   ☐ echo-agent                             │
│                                          [發一把新的 key]   │
│                                                            │
│ ┌ 這是「Dify 正式環境」的 key，只會出現這一次 ─────────────┐ │  ← 發完才出現，要自己關
│ │ zmcp_live_xxxxxxxx…                          [複製]    │ │
│ └ [我已經複製好了] ───────────────────────────────────────┘ │
│                                                            │
│ Dify 正式環境  zmcp_live_a1b2…  建立時間  最後使用  ●可用   │  ← key 列表
│   開放 1 個 Agent：stock-agent         [編輯勾選] [撤銷]    │
│ t1             zmcp_live_iwc3…  …       …        ●已撤銷    │  ← 已撤銷的灰掉，留著
└────────────────────────────────────────────────────────────┘
```

## 元件一覽

| 元件 | 職責與要點 |
|---|---|
| `agent-list.js` | 總覽卡片格子。整張卡可以點進詳情；卡上**不放** Agent ID 和任何其他可互動的元素。名稱首字方塊、描述最多兩行、最新版本（沒有就寫「尚無已發布版本」） |
| `create-agent-dialog.js` / `edit-agent-dialog.js` / `delete-agent-dialog.js` | 原生 `<dialog>`。刪除要講明「不可復原、稽核紀錄一起消失」 |
| `version-registration.js` | semver + image_ref。送出前開 `<dialog>` 確認，說明「同版號會在驗證通過後取代現有 image，舊 image 會被刪除」。`202` 後每 2 秒輪詢到終態；`409` 顯示「這個版號正在驗證中」；FAILED 的行動按鈕是「修正後重新註冊」（同版號可以） |
| `agent-version-list.js` | 版本與 artifact 摘要。同一個版號可能同時看到 PUBLISHED 和最近一次失敗的 FAILED |
| `run-composer.js` | 選版本和 entrypoint、依 input schema 填資料、建 Run。只在有 PUBLISHED 版本時可用 |
| `endpoints-card.js` | REST / OpenAPI 兩個網址 + Copy（MCP 是平台單一接口，網址在 `/console/mcp`） |
| `mcp-keys-card.js` | MCP 頁主體：接口網址 + Copy；發 key（名稱 + 勾選 Agent，明文**只顯示這一次**）；key 列表含已撤銷的，每把可改勾選、可撤銷。勾選清單來自 `GET /v1/agents`，名稱一律用 `textContent` |
| `recent-runs.js` | 這個 Agent 最近的 Runs |
| `run-timeline.js` | **最重要的元件**：事件依時間排列、預設收合、展開看完整 payload（超過 2KB 就折疊並顯示大小）。**只追加**，不重畫整個列表。預設只渲染最近 200 筆，頂端有「載入更早的事件」 |
| `run-graph.js` | Run 的流程圖版面：畫「這次實際走過的路」（依 `node.start` 第一次出現的順序單列橫排，跑多次標 `×N`，回頭的邊畫成上方虛線弧 = retry 迴圈）。**手寫 inline SVG**，用自然像素寬度 + 外層橫向捲動。**不自己訂閱 SSE**，和時間軸共用同一份事件 |
| `llm-credential-card.js` | 只顯示尾四碼：寫得進去、讀不出來 |
| `containers-card.js` | 常駐容器狀態、`inflight / max`、`restart_count`、預熱 / 重啟 / 停止 |
| `api-keys-card.js` | 發 key 時明文**只顯示這一次**並提示複製；列表含已撤銷的 |
| `usage-panel.js` | 用量總計、依 key、依 model，逐筆呼叫可篩選錯誤 |
| `agent-config-card.js` | 整份 JSON 編輯、整份取代；提示「設定不是機密，會完整進容器」 |
| `agent-secrets-card.js` | 只顯示名字和尾四碼；刪除被引用的機密時顯示 `referenced_by` |
| `egress-card.js` | 對外白名單與寄件者 |
| `egress-calls-panel.js` | 對外請求稽核列表，可依 kind / 錯誤篩選 |

## Run 觀測頁（`pages/run.js`）

- 用瀏覽器原生的 `EventSource` 接 `GET /v1/runs/{id}/events`：它自己會帶 `Last-Event-ID` 重連，**不要另外寫一套**。
  要處理的：用 `id` 去重、斷線時顯示「重新連線中」、離開頁面要 `close()`。
- token 串流：**用陣列累積 + `requestAnimationFrame` 節流後一次寫入（約 60ms）**。每個 token 都碰 DOM 會把頁面拖垮。只用 `textContent` / `append`。串流區加 `aria-live="polite"`。
- 時間軸和流程圖兩種版面，用 `role="group"` 的切換鈕、`aria-pressed` 表示目前是哪個。
- ⚠️「停止」按鈕會呼叫 `POST /v1/runs/{id}/cancel`，但**後端還沒實作**（會 404），看 [api.md](api.md)。

## `api.js` 的紀律

- 所有請求都走 `req()`：base 是 `/v1`、非 2xx 丟 `ApiError`（帶 `code` / `run_id` / `retryable` / `details`）、`204` 回 `null`。
- POST / PUT 固定帶 `Content-Type: application/json`。
- `createRun()` **不得**在 body 加 `trigger`，也不得送 `X-Zentra-Trigger`——trigger 由伺服器決定。
- 沒有型別檢查，這一層的紀律是唯一的防線。

## 無障礙（不可妥協）

- 所有互動元素都要能用鍵盤操作；focus 用 2px outline + 2px offset，要清楚看得見。
- 文字對比 ≥ 4.5:1；disabled 色只能用在「非必要資訊」。
- **優先用原生元素**：`<button>` / `<dialog>`（用 `showModal()`）/ `<details>` / `<a>`。`<div onclick>` 或自製的 `role="dialog"` 一律退回。
- 狀態不能只靠顏色表達。
