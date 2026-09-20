# UI 契約 (UI Contract)

| 欄位 | 值 |
|---|---|
| 契約版本 | `v0.4.0` |
| 擁有者 | `@ui-designer` |
| 對齊 Spec | `specs/SPEC.md v0.2.1` |
| 設計來源 | Stock-Insight-Chat 既有設計語言 + ChatGPT 極簡風 |

> **`@frontend-dev` 只能引用這份文件的 token，不得寫死色碼、間距、字級。**
> 要新增 token 或元件 → 開 `SPEC-QUESTION`。

> **v0.4.0 依 [ADR-0006](./adr/ADR-0006-vanilla-frontend.md)：改用原生 HTML + CSS + ES modules，零建置。**
> 沒有 React、沒有 Tailwind、沒有建置步驟、沒有前端路由器。
> **§0–§4 的設計語言、token、狀態語言、五態規範一個字都沒改** ——
> 這些 token 本來就是純 CSS custom properties，直接貼進 `tokens.css` 就能用。
> 改的是 §5（元件規格改寫成 ES module）與新增的 §6（前端結構與實作約束）。

> **Console 五個畫面**（[ADR-0005](./adr/ADR-0005-mvp-single-host-scope.md) 降維後）。
> 網址由 `api` 的五條 `FileResponse` 路由給出，**不是前端路由器**：
>
> | # | 網址 | 檔案 | 畫面 |
> |---|---|---|---|
> | 1 | `/console/agents` | `agents.html` | Agent 列表 |
> | 2 | `/console/agents/{agent_id}` | `agent.html` | Agent 詳情：版本列表、`endpoints`（REST / MCP / OpenAPI）、最近 Runs |
> | 3 | `/console/agents/{agent_id}/versions/new` | `version-new.html` | 上傳新版本（拖放 `.tar.gz`，輪詢 `BUILDING → PUBLISHED`） |
> | 4 | `/console/runs` | `runs.html` | Run 列表（篩選 + cursor 分頁） |
> | 5 | `/console/runs/{run_id}` | `run.html` | **Run 觀測** —— 產品最重要的畫面 |
>
> `/` 轉址到 `/console/agents`。每個頁面從 `location.pathname` 讀出自己的 id。
> 建立精靈與 Flow 畫布**已移除**，對應功能在 MVP 外。

---

## 0. 設計原則（先讀這段，再看 token）

Zentra Console 延續 Stock-Insight-Chat 的視覺語言：**深色、近乎單色、克制**。

| 原則 | 具體含義 |
|---|---|
| **單色優先** | 主色是 `#b0b0b0` 高級灰，不是彩色。**顏色只用來傳達狀態語意**，不用來裝飾 |
| **表面靠明度分層** | 用 `rgba(255,255,255,0.02~0.05)` 疊出層次，**不用陰影**（除了 modal / dropdown） |
| **髮絲邊框** | 邊框是 `#2a2a2a` 或更淡的白色透明疊加，不要高對比描邊 |
| **留白即設計** | 寧可多留白，不要塞滿。密度靠 token 控制，不靠壓縮間距 |
| **克制的動態** | 統一 `0.2s`。只有「進行中」的東西才動 |

### ⛔ 絕對禁止

1. **不准用 emoji 當圖示**（⏳ ⚙️ ✋ ✅ ❌ 🔴 這類一律禁止）。
   圖示一律用 **Lucide 的線性 SVG，`stroke-width="1.5"`**（內嵌 sprite，見 §3），或**純色圓點**。
2. **不准用彩色漸層、陰影光暈、彩色按鈕**做裝飾。
3. **不准自創尺寸**。所有間距、字級、圓角、高度都必須引用下方 token。
4. **不准只靠顏色表達狀態** —— 每個狀態都必須同時有文字標籤。

---

## 1. 設計 Token

> **這一整節原封不動放進 `apps/console/static/css/tokens.css`。**
> 它從第一天就是純 CSS custom properties，所以換掉 Tailwind 沒有任何遷移成本。
> **所有其他 CSS 只准引用變數，不准出現字面值色碼、間距、字級。**

### 1.1 色彩（深色，MVP 不做淺色模式）

```css
:root {
  /* 底層 */
  --bg-base:          #0d0d0d;   /* 頁面底色 */
  --bg-sidebar:       #131313;
  --bg-elevated:      #1a1a1a;   /* modal / dropdown / popover */
  --bg-code:          #0a0a0a;

  /* 表面（疊加式，不用實色） */
  --surface-1:        rgba(255, 255, 255, 0.02);   /* 面板內容區 */
  --surface-2:        rgba(255, 255, 255, 0.03);   /* 卡片 */
  --surface-3:        rgba(255, 255, 255, 0.04);   /* 輸入框 */
  --surface-hover:    rgba(255, 255, 255, 0.05);
  --surface-active:   rgba(255, 255, 255, 0.08);

  /* 邊框 */
  --border-subtle:    rgba(255, 255, 255, 0.04);
  --border-default:   #2a2a2a;
  --border-strong:    #3a3a3a;
  --border-focus:     #b0b0b0;

  /* 文字 */
  --text-primary:     #e0e0e0;
  --text-secondary:   #888888;
  --text-disabled:    #5a5a5a;

  /* 主色 —— 高級灰，不是品牌彩色 */
  --accent:           #b0b0b0;
  --accent-hover:     #c8c8c8;
  --accent-subtle:    rgba(176, 176, 176, 0.08);

  /* 狀態色 —— 刻意降飽和度以貼合近黑底色 */
  --status-queued:     #6b6b6b;
  --status-scheduling: #8a8a8a;
  --status-running:    #4a8fd4;
  --status-waiting:    #c9a04a;
  --status-succeeded:  #4a9070;
  --status-failed:     #c25651;
  --status-cancelled:  #4a4a4a;

  --danger:  #c25651;
  --warning: #c9a04a;
  --success: #4a9070;
}
```

> **只有 §1.4 的狀態指示器可以用狀態色。** 其他地方一律單色。

### 1.2 字體與字級

```css
--font-sans:    'Inter', -apple-system, 'Noto Sans TC', sans-serif;
--font-display: 'Outfit', 'Inter', sans-serif;     /* 只用於標題與品牌 */
--font-mono:    'SF Mono', 'Fira Code', Consolas, monospace;

--text-2xs: 11px;   /* 標籤、角標 */
--text-xs:  12px;   /* 次要說明、表頭 */
--text-sm:  13px;   /* ← 預設內文 */
--text-md:  14px;   /* 強調內文、輸入框 */
--text-lg:  16px;   /* 區塊標題 */
--text-xl:  20px;   /* 頁面標題 */
--text-2xl: 28px;   /* 大標（僅首頁 / 空狀態） */

--leading-tight:  1.3;   /* 標題 */
--leading-normal: 1.5;   /* 內文 */
--leading-relaxed:1.7;   /* 長篇閱讀、LLM 輸出 */

--weight-normal: 400;
--weight-medium: 500;   /* 強調用這個，不要用 600+ */
```

**規則**：`ID`、`run_id`、payload、log、數值一律 `--font-mono`。中英混排以 Inter 為主，中文落 Noto Sans TC。

### 1.3 間距與幾何（比例的權威定義）

```css
/* 間距：4px 基準，只用這幾階，不准插值 */
--space-1: 4px;   --space-2: 8px;   --space-3: 12px;  --space-4: 16px;
--space-5: 20px;  --space-6: 24px;  --space-8: 32px;  --space-12: 48px;  --space-16: 64px;

/* 圓角 */
--radius-xs:  4px;    /* 標籤、角標 */
--radius-sm:  6px;    /* 小按鈕、輸入框 */
--radius-md:  8px;    /* ← 預設 */
--radius-lg:  10px;   /* 卡片、面板 */
--radius-xl:  12px;   /* modal */
--radius-full:999px;  /* 膠囊、頭像 */

/* 控制項高度 —— 固定三階，不准自創 */
--control-sm: 28px;
--control-md: 32px;   /* ← 預設按鈕、輸入框 */
--control-lg: 40px;   /* 主要 CTA、搜尋列 */

/* 圖示尺寸 */
--icon-sm: 14px;   /* 內文行內 */
--icon-md: 16px;   /* ← 預設，按鈕內 */
--icon-lg: 18px;   /* 側欄導覽 */

/* 版面 */
--sidebar-width:   260px;
--sidebar-collapsed: 0;
--content-max:     1200px;   /* 列表、表格 */
--reading-max:     768px;    /* 表單、文件、LLM 輸出 */
--row-height:      44px;     /* 列表列 */
--row-height-compact: 36px;  /* 事件流、log */
--header-height:   52px;

--transition: 0.2s;
```

**比例規則（避免版面失衡）**：
- 卡片內距 `--space-4`，卡片之間 `--space-3`
- 頁面左右留白至少 `--space-6`，行動裝置 `--space-4`
- 圖示與文字間距固定 `--space-2`
- 任何區塊的內距不得小於其內部元素間距
- **禁止**在同一畫面出現三種以上的圓角值

---

## 2. Run 狀態的視覺語言

**用「圓點 + 文字標籤」，不用圖示，更不用 emoji。**
圓點 `6px`，`border-radius: var(--radius-full)`，與標籤間距 `--space-2`。

| 狀態 | 圓點色 | 標籤（繁中） | 動態 | 補充要求 |
|---|---|---|---|---|
| `QUEUED` | `--status-queued` | 排隊中 | 無 | — |
| `SCHEDULING` | `--status-scheduling` | 配置中 | 緩慢脈動 2s | — |
| `RUNNING` | `--status-running` | 執行中 | 脈動 1.5s | **必須同時顯示當前節點名稱與已耗時** |
| `SUCCEEDED` | `--status-succeeded` | 已完成 | 無 | — |
| `FAILED` | `--status-failed` | 失敗 | 無 | **必須顯示 `error.code`；`retryable` 時提供重試** |
| `CANCELLED` | `--status-cancelled` | 已取消 | 無 | 標籤用 `--text-disabled`，與 FAILED 明確區分 |

```
● 執行中 · llm_node · 12s          ← RUNNING
● 已完成 · 8.4s                    ← SUCCEEDED
● 失敗 · SANDBOX_TIMEOUT           ← FAILED
```

`prefers-reduced-motion` 時脈動改為靜態，用明度差異表示活躍。

> `WAITING_INPUT`（HITL）已於 SPEC v0.2.0 移出 MVP，狀態枚舉不再有這個值——
> **不要渲染它，也不要保留對應的 `--status-waiting` 使用處**（token 本身留著，之後加回來時可直接用）。

---

## 3. 圖示規範

**沒有 npm，所以不裝 `lucide-react`。** 從 [lucide.dev](https://lucide.dev) 複製需要的那幾個 SVG，
手工併成**一份 sprite**：`apps/console/static/icons.svg`。

```html
<!-- icons.svg（節錄）—— 每個圖示一個 <symbol>，viewBox 統一 0 0 24 24 -->
<svg xmlns="http://www.w3.org/2000/svg" style="display:none">
  <symbol id="i-bot" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
    <path d="..."/>
  </symbol>
</svg>
```

```html
<!-- 用法 —— 尺寸靠 CSS，顏色靠 currentColor -->
<svg class="icon icon-md"><use href="/console/static/icons.svg#i-bot"/></svg>
```

```css
.icon    { width: var(--icon-md); height: var(--icon-md); color: var(--text-secondary);
           transition: color var(--transition); flex: none; }
.icon-sm { width: var(--icon-sm); height: var(--icon-sm); }
.icon-lg { width: var(--icon-lg); height: var(--icon-lg); }
a:hover .icon, button:hover .icon { color: var(--text-primary); }
```

- **顏色**：一律 `currentColor`，預設 `--text-secondary`，hover 轉 `--text-primary`
- **禁止**：emoji、彩色圖示、填充式圖示、混用多種圖示庫、把 SVG 直接貼進每個 HTML（要用 sprite）

MVP 只需要這幾個（**不要多抓**，sprite 要小）：

| 用途 | Lucide 名稱 | symbol id |
|---|---|---|
| Agent | `Bot` | `i-bot` |
| Run / 執行紀錄 | `Activity` | `i-activity` |
| 沙盒 / 版本 | `Box` | `i-box` |
| 上傳 | `Upload` | `i-upload` |
| 複製 | `Copy` | `i-copy` |
| 展開 / 收合 | `ChevronRight` / `ChevronDown` | `i-chevron-right` / `i-chevron-down` |
| 重試 | `RotateCw` | `i-rotate-cw` |
| 停止 | `Square` | `i-square` |
| 搜尋 | `Search` | `i-search` |

---

## 4. 元件五態規範

**每個元件規格都必須定義五種狀態，缺一不准交付。**

| 狀態 | 規則 |
|---|---|
| Loading | 已知版面用骨架屏（`--surface-2` + 1.5s 微光）。**超過 3 秒必須顯示進度語意**（在做什麼、第幾步） |
| Empty | 一行說明「這裡會出現什麼」+ 一個 secondary 按鈕。文字 `--text-secondary`，置中，上下留白 `--space-12`。**不要只寫「無資料」** |
| Error | 必須含 `error.code`（mono 字體）、`run_id`、以及「我現在該做什麼」。`retryable` 時提供重試 |
| Streaming | 見 §5.2 |
| Success | 正常態 |

---

## 5. 核心元件規格

**沒有元件框架，所以「元件」＝ 一個 ES module，匯出一個 `mount(el, props)` 函式。**
沒有 diff、沒有響應式；狀態變了就整塊重繪，**唯一的例外是事件流（只追加）**。

```js
// 所有元件的統一形狀 —— apps/console/static/js/components/*.js
export function mount(el, props) {
  el.innerHTML = render(props);      // 樣板字串，一次寫入
  bind(el, props);                   // 事件委派綁在 el 上，不是每個子節點
  return { update(next) {…}, destroy() {…} };   // destroy 要關掉 EventSource / clearInterval
}
```

> **離開頁面沒有解除綁定就是 bug。** vanilla 沒有 `useEffect` 幫你清，
> 每個 `mount` 回傳的 `destroy()` 必須在 `beforeunload` 與切換時被呼叫。

### 5.1 `shell.js` — 共用外框

五個 HTML 檔各自只寫 `<main id="app">`，外框由這個 module 注入，**不要複製貼上五份側欄**。

- 左側欄 `--sidebar-width` `--bg-sidebar`，右側內容 `--bg-base`，中間 1px `--border-default`
- 側欄項目：高度 `--control-lg`，內距 `--space-3`，圓角 `--radius-md`，hover `--surface-hover`，
  選中 `--surface-active` + 文字轉 `--text-primary`
- 選中狀態由 `location.pathname` 判斷（`startsWith`），**不用手動傳 active prop**
- 頂部列 `--header-height`，只放麵包屑與當前頁動作，**不放品牌** logo（品牌在側欄頂）
- 側欄可收合；狀態存 `localStorage`，收合後內容區左右留白補到 `--space-8`

### 5.2 `streaming-output.js` — 串流輸出

- 內容寬度上限 `--reading-max`，行高 `--leading-relaxed`
- 逐字渲染：**用陣列累積 + `requestAnimationFrame` 節流 flush（約 60ms）**，
  每個 token 都碰 DOM 會把頁面打死
- 只用 `textContent` / `append`，**絕對不要用 `innerHTML` 接 LLM 輸出**（XSS）
- 游標：2px 寬 `--accent`，1s 閃爍；串流結束立即移除
- 右上角常駐「停止」（`i-square` + 文字），送 `POST /v1/runs/{id}/cancel`，**不是只 `close()` 本地連線**
- `EventSource` 原生就會自動重連並帶 `Last-Event-ID`；斷線時顯示 `--text-secondary` 的「重新連線中」一行
- `aria-live="polite"`

### 5.3 `run-timeline.js` — 產品最重要的元件

- 事件依時間排列，列高 `--row-height-compact`，**預設收合**，`i-chevron-right` 展開
- 每列：`時間(mono, --text-2xs) · 事件類型 · 摘要`
- 展開後顯示完整 payload，`--bg-code` 底、mono 字體、右上角 `Copy`
- **只追加渲染**：新事件 `append` 一列，**不要重畫整個列表**
- 大 payload（> 2KB）折疊並顯示大小，點擊才展開
- **無虛擬捲動**（[ADR-0006](./adr/ADR-0006-vanilla-frontend.md) 的已知降級）：
  預設只渲染**最近 200 筆**，頂端放「載入更早的事件」按鈕，每次再取 200 筆。
  超過 2000 筆時提示使用者改用 API 拉完整事件流
- **目標：工程師從「這次跑壞了」到「哪個節點回了什麼」不超過 30 秒**

### 5.4 `version-upload.js` — 上傳新版本

- 拖放區佔內容寬度 `--reading-max`，虛線 `--border-default`，拖曳中轉 `--border-focus`
- 只收 `.tar.gz`；型別或大小不符當場顯示，**不要等送出才報錯**
- `FormData` + `fetch` 送 `POST /v1/agents/{id}/versions`（multipart）
- 送出後以 `setInterval`（2s）輪詢版本狀態：`BUILDING`（進行中樣式）→ `PUBLISHED` / `FAILED`。
  **`destroy()` 一定要 `clearInterval`**
- `FAILED` 必須顯示 `error.message` 全文，可 `Copy` —— build 失敗的原因是使用者唯一的除錯線索
- **發布不可逆**（P5：同一個 semver 不能重上）→ 原生 `<dialog>` 二次確認，明講版號與檔案 SHA

### 5.5 `endpoints-card.js` — 三個門面

- 顯示該 Agent 的三個門面：`rest` / `mcp` / `openapi`，每個一行 mono + `Copy`
- 這是 P3 的使用者可見面，**三個都要在，不能只顯示 REST**
- `Copy` 用 `navigator.clipboard.writeText`，成功後按鈕文字暫時轉「已複製」1.5s

### 5.6 按鈕（CSS class，不是元件）

| class | 樣式 |
|---|---|
| `.btn-primary` | `--accent` 底、`--bg-base` 文字。**全頁最多一個** |
| `.btn-secondary` | `--surface-3` 底、`--border-default` 邊、`--text-primary` 文字 |
| `.btn-ghost` | 透明底，hover `--surface-hover` |
| `.btn-danger` | 透明底、`--danger` 文字與邊框；hover 才填 `--danger` |

高度 `--control-md`（CTA 用 `--control-lg`），左右內距 `--space-4`，圓角 `--radius-md`。

---

## 6. 前端結構與實作約束

### 6.1 檔案配置

```
apps/console/
  agents.html  agent.html  version-new.html  runs.html  run.html
  static/
    icons.svg                    ← §3 的 sprite
    css/
      tokens.css                 ← §1 原封不動
      base.css                   ← reset、排版、.btn-*、.icon
      layout.css                 ← shell、列表、卡片
    js/
      api.js                     ← fetch 包裝：base URL、錯誤格式、欄位名只在這裡出現
      store.js                   ← 約 40 行發布／訂閱，只有 run.html 需要
      shell.js                   ← §5.1
      components/
        streaming-output.js  run-timeline.js  version-upload.js  endpoints-card.js
      pages/
        agents.js  agent.js  version-new.js  runs.js  run.js
```

每個 `.html` 只做三件事：載入 CSS、`<main id="app">`、`<script type="module" src="…/pages/x.js">`。

### 6.2 `api.js` 是契約的唯一出口

**`api-contract.md` 的欄位名只准出現在 `api.js` 裡一次。** 頁面拿的是它回傳的物件。

```js
const BASE = '/v1';
export class ApiError extends Error {
  constructor(e) { super(e.message); Object.assign(this, e); }   // code / run_id / retryable
}
async function req(path, opts = {}) {
  const r = await fetch(BASE + path, opts);
  if (!r.ok) throw new ApiError((await r.json()).error);         // 統一錯誤格式，SPEC §8.4
  return r.status === 204 ? null : r.json();
}
export const listRuns   = (q)  => req('/runs?' + new URLSearchParams(q));
export const getRun     = (id) => req(`/runs/${id}`);
export const cancelRun  = (id) => req(`/runs/${id}/cancel`, { method: 'POST' });
```

沒有型別檢查（ADR-0006 的已知代價），**所以這層的紀律是唯一的防線**。
頁面裡出現 `data.run_id` 以外的手打欄位名 → 退回。

### 6.3 硬性規則

1. **不准引入任何執行期函式庫**（React / Vue / Alpine / htmx / jQuery / lodash 全部不准）。
   覺得需要 → 開 `SPEC-QUESTION`，不要自己裝。
2. **不准有建置步驟。** 瀏覽器直接吃 `.html` / `.css` / `.js`。沒有 `package.json`。
3. **不准用 `innerHTML` 寫入任何來自 API 或使用者的字串。** 樣板字串只用於固定結構，
   動態內容一律 `textContent`。這是 vanilla 最容易出的 XSS。
4. **不准寫前端路由器。** 網址由 `api` 的五條路由給出。
5. **不准寫死色碼、間距、字級。** 只引用 §1 的 CSS 變數。
6. **每個 `mount()` 都要能 `destroy()`**，並且真的關掉 `EventSource` 與 `setInterval`。

### 6.4 認證（MVP 無）

MVP 不做認證（與 ZEN-000 一致）。之後補的時候走**同源 session cookie**
（`HttpOnly` + `SameSite=Lax`）。
**不要把 `api_key` 放 `localStorage`** —— 它是長期憑證，一個 XSS 就整把拿走。

---

## 7. 無障礙底線（不可妥協）

- 所有互動元素可鍵盤操作，focus 用 2px `--border-focus` outline + 2px offset，**清楚可見**
- 文字對比 ≥ 4.5:1。`--text-secondary` (#888) 在 `--bg-base` (#0d0d0d) 上約 5.4:1 ✓；
  **`--text-disabled` 只能用於非必要資訊**
- 狀態不能只靠顏色 —— §2 每個狀態都配了文字標籤
- 串流區塊 `aria-live="polite"`
- **原生元素優先**：`<button>` / `<dialog>` / `<details>` / `<a>`。
  用 `<div onclick>` 假裝按鈕 → 鍵盤與螢幕報讀器全部失效，一律退回
- 尊重 `prefers-reduced-motion`

---

## 8. 變更紀錄

| 版本 | 日期 | 變更 | 由 |
|---|---|---|---|
| v0.1.0 | 2026-09-20 | 初版 | @ui-designer |
| v0.2.0 | 2026-09-20 | 改採 Stock-Insight-Chat 深色單色設計語言；**移除全部 emoji 圖示**，狀態改用圓點 + 文字；補齊尺寸/比例 token（控制項高度、圖示尺寸、版面寬度） | @ui-designer |
| v0.3.0 | 2026-09-20 | 依 ADR-0005 降維：Console 收斂成五個畫面；移除 `<AgentWizard>` 與 `<FlowCanvas>`；新增 `<VersionUpload>` 與 `<EndpointsCard>`。設計 token 與無障礙底線不變 | @ui-designer |
| v0.4.0 | 2026-09-20 | 依 [ADR-0006](./adr/ADR-0006-vanilla-frontend.md) 改用原生 HTML + CSS + ES modules（零建置）：§5 元件規格改寫成 `mount(el, props)` module；新增 §6 前端結構與實作約束（檔案配置、`api.js` 契約出口、六條硬性規則、認證）；§3 圖示改為自製 SVG sprite；RunTimeline 的虛擬捲動降級為分頁載入。**§0–§4 的設計語言、token、狀態語言、五態規範完全不變** | @ui-designer |
