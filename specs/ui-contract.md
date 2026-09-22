# UI 契約 (UI Contract)

| 欄位 | 值 |
|---|---|
| 契約版本 | `v0.5.1` |
| 擁有者 | `@ui-designer` |
| 對齊 Spec | `specs/SPEC.md v0.3.1` |
| 設計來源 | Stock-Insight-Chat 既有設計語言 + ChatGPT 極簡風 |

> **`@frontend-dev` 只能引用這份文件的 token，不得寫死色碼、間距、字級。**
> 要新增 token 或元件 → 開 `SPEC-QUESTION`。

> **v0.4.0 依 [ADR-0006](./adr/ADR-0006-vanilla-frontend.md)：改用原生 HTML + CSS + ES modules，零建置。**
> 沒有 React、沒有 Tailwind、沒有建置步驟、沒有前端路由器。
> **§0–§4 的設計語言、token、狀態語言、五態規範一個字都沒改** ——
> 這些 token 本來就是純 CSS custom properties，直接貼進 `tokens.css` 就能用。
> 改的是 §5（元件規格改寫成 ES module）與新增的 §6（前端結構與實作約束）。

> **v0.5.0 依 [ADR-0008](./adr/ADR-0008-oci-image-agent-packaging.md)：Agent 版本改為註冊 OCI image。**
> Console 不接收 image tar、不代 build Dockerfile，也不收 Registry credential。開發者先把 image push 到
> VM 可存取的 Registry，再以 canonical `repository@sha256:<64 hex>` 註冊不可變版本。

> **v0.5.1 對齊 SPEC / API v0.3.1 的 Run trigger 裁決。** Console 經 `/v1` 建立 Run，server 固定記為
> `API`；只有 MCP `tools/call` 記為 `MCP`。Console 不送 `trigger` request 欄位或識別 header。

> **Console 五個畫面**（[ADR-0005](./adr/ADR-0005-mvp-single-host-scope.md) 降維後）。
> 網址由 `api` 的五條 `FileResponse` 路由給出，**不是前端路由器**：
>
> | # | 網址 | 檔案 | 畫面 |
> |---|---|---|---|
> | 1 | `/console/agents` | `agents.html` | Agent 列表；以頁內 `<dialog>` 建立 Agent metadata |
> | 2 | `/console/agents/{agent_id}` | `agent.html` | Agent 詳情：版本列表、呼叫區、`endpoints`（REST / MCP / OpenAPI）、最近 Runs |
> | 3 | `/console/agents/{agent_id}/versions/new` | `version-new.html` | 註冊 canonical OCI image digest，輪詢 `BUILDING → PUBLISHED / FAILED` |
> | 4 | `/console/runs` | `runs.html` | Run 列表（篩選 + cursor 分頁） |
> | 5 | `/console/runs/{run_id}` | `run.html` | **Run 觀測** —— 產品最重要的畫面 |
>
> `/` 轉址到 `/console/agents`。每個頁面從 `location.pathname` 讀出自己的 id。
> 建立精靈與 Flow 畫布**已移除**，對應功能在 MVP 外。
> 「建立 Agent」是單一步驟 metadata 表單，不是第六個頁面或 Agent 組裝精靈。

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

> **只有 §2 的狀態指示器可以用狀態色。** 其他地方一律單色。

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

### 2.1 Agent Version 狀態

Version 狀態同樣使用「6px 圓點 + 文字標籤」，不可與 Run 狀態混寫，也不可只顯示顏色。

| 狀態 | 圓點色 | 標籤（繁中） | 畫面行為 |
|---|---|---|---|
| `BUILDING` | `--status-scheduling` | 驗證中 | 輪詢版本詳情；顯示已耗時與「平台正在拉取、檢查並驗證 image」 |
| `PUBLISHED` | `--status-succeeded` | 已發布 | 顯示完整 digest、platform、size、runtime contract 與 entrypoints；可建立 Run |
| `FAILED` | `--status-failed` | 驗證失敗 | 顯示 `BUILD_FAILED`、`error.message` 全文與 `details.stage`（`PULL` / `INSPECT` / `VALIDATE`） |

`BUILDING` 期間 API 沒有回傳即時 stage，因此 UI 不得猜測目前是 `PULL`、`INSPECT` 或 `VALIDATE`。
超過 3 秒後，使用聚合進度文案「平台正在拉取、檢查並驗證 image · 已耗時 12s」；三個 stage 只在
`FAILED` 的 `error.details.stage` 中標示精確失敗位置。離開註冊頁不會中止後端工作，Agent 詳情頁仍可查看狀態。

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
| 沙盒 / 版本 / 註冊版本 | `Box` | `i-box` |
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
| Error | 必須含 `error.code`（mono 字體）、識別碼與「我現在該做什麼」。Run 錯誤顯示 `run_id`；版本錯誤顯示 `agent_version_id`；建立 Agent 尚無資源 ID 時顯示欄位錯誤。`retryable=true` 才提供原請求重試 |
| Partial / Streaming | 資料部分抵達或非同步工作未完成時保留已知內容並標明進度；Run token 串流另見 §5.2 |
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

| 狀態 | 呈現與動作 |
|---|---|
| Loading | shell 先同步顯示，內容 slot 提供頁面骨架；導覽不可因資料請求被鎖住 |
| Empty | shell 本身不產生空狀態，由目前 page component 放入內容 slot |
| Error | page error 留在內容 slot；側欄與麵包屑仍可導航 |
| Partial / Streaming | 內容更新不重畫 shell；側欄收合狀態保持 |
| Success | 顯示完整 header、sidebar 與 page slot |

### 5.2 `streaming-output.js` — 串流輸出

- 內容寬度上限 `--reading-max`，行高 `--leading-relaxed`
- 逐字渲染：**用陣列累積 + `requestAnimationFrame` 節流 flush（約 60ms）**，
  每個 token 都碰 DOM 會把頁面打死
- 只用 `textContent` / `append`，**絕對不要用 `innerHTML` 接 LLM 輸出**（XSS）
- 游標：2px 寬 `--accent`，1s 閃爍；串流結束立即移除
- 右上角常駐「停止」（`i-square` + 文字），送 `POST /v1/runs/{id}/cancel`，**不是只 `close()` 本地連線**
- `EventSource` 原生就會自動重連並帶 `Last-Event-ID`；斷線時顯示 `--text-secondary` 的「重新連線中」一行
- `aria-live="polite"`

| 狀態 | 呈現與動作 |
|---|---|
| Loading | 尚未收到 token 時顯示「等待輸出」與已耗時；不使用空白面板 |
| Empty | Run 成功但沒有文字 token 時顯示「此 Run 沒有文字串流輸出」，並引導查看 output / timeline |
| Error | 顯示 Run `error.code`、`run_id` 與下一步；retryable 時提供建立新 Run 的動作 |
| Partial / Streaming | 節流追加 token、顯示游標與停止按鈕；斷線保留文字並顯示重新連線狀態 |
| Success | 移除游標與停止按鈕，完整文字保持可選取與複製 |

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

| 狀態 | 呈現與動作 |
|---|---|
| Loading | 初始事件載入顯示固定列骨架 |
| Empty | 尚未有事件時顯示「等待第一個執行事件」；終態仍無事件則提示重新整理 Run |
| Error | 顯示串流／歷史讀取錯誤、`run_id` 與重連或重新載入動作；保留已載入事件 |
| Partial / Streaming | 只 append 新事件；重連與載入更早事件都不清空現有列 |
| Success | 收到終局 `run.status` 後停止 live 標示；全部事件仍可展開與複製 |

### 5.4 `version-registration.js` — 註冊 OCI image 版本

位於 `/console/agents/{agent_id}/versions/new`，內容寬度 `--reading-max`。這是文字表單，沒有檔案輸入、
拖放區、upload progress、Registry 密碼或 Docker build 控制項。

- `semver`：必填文字欄，說明「同一 Agent 內不可重用；發布後不可修改」。
- `image_ref`：必填 mono 多行欄，placeholder 使用 canonical 範例
  `ghcr.io/acme/research-agent@sha256:<64 hex>`；本地先檢查必須含 `@sha256:` 與 64 位 hex，後端仍是權威。
- 欄位下固定提示：「請先將 image push 到 VM 可存取的 Registry。私有 Registry credential 由 VM 維運者預先配置，Console 不接收密碼。」
- 點「註冊並驗證」先開原生 `<dialog>`，逐行顯示 Agent 名稱、`semver`、完整 `image_ref`，並說明
  「此版本號與 digest 註冊後不可覆寫」。只有 dialog 的確認按鈕送出 JSON `POST /v1/agents/{id}/versions`。
- 收到 `202` 後鎖定欄位，每 2 秒輪詢版本詳情。`destroy()` 必須 `clearInterval`；終態也要立即停止輪詢。
- `BUILDING` 顯示聚合進度、已耗時與三個固定步驟說明「拉取 exact digest → inspect runtime → 隔離驗證」，
  但不可把其中任何一步標成目前 stage，因 API 未提供即時 stage。
- `PUBLISHED` 顯示完整 `artifact.digest`、`platform`、格式化後 size、`runtime_contract` 與 entrypoint 數量；
  primary CTA「前往 Agent 詳情」。Copy 一律複製完整值，不能複製短碼。
- `FAILED` 顯示 `BUILD_FAILED`、`error.message` 全文、`details.stage`、`agent_version_id` 與完整 image ref，
  並提供 Copy。`BUILD_FAILED` 不可重試原請求；CTA 是「返回 Agent 詳情」與「改用新版本號重新註冊」。
- `409 VERSION_CONFLICT` 明示該 semver 已存在且不可覆寫；`422 VALIDATION_ERROR` 將
  `details` 對應到欄位。不得自創其他錯誤碼。

五態：

| 狀態 | 呈現與動作 |
|---|---|
| Loading | 初次載入 Agent metadata 時以表單骨架顯示；送出中按鈕改「註冊中」並 disabled，避免重送 |
| Empty | `semver` 與 `image_ref` 空白表單；主 CTA disabled，說明先 push 再貼 canonical digest |
| Error | 顯示 API `error.code`、訊息與可行動建議；若已建立版本則同時顯示 `agent_version_id` |
| Partial / Streaming | `BUILDING` 輪詢態；保留 `202` 已知 metadata，超過 3 秒顯示驗證工作內容與已耗時 |
| Success | `PUBLISHED` 摘要與前往詳情 CTA；輪詢已停止 |

### 5.5 `endpoints-card.js` — 三個門面

- 顯示該 Agent 的三個門面：`rest` / `mcp` / `openapi`，每個一行 mono + `Copy`
- 這是 P3 的使用者可見面，**三個都要在，不能只顯示 REST**
- `Copy` 用 `navigator.clipboard.writeText`，成功後按鈕文字暫時轉「已複製」1.5s

五態：

| 狀態 | 呈現與動作 |
|---|---|
| Loading | 三行固定高度骨架 |
| Empty | 尚無 `PUBLISHED` 版本時保留三門面區位置，說明「發布版本後即可呼叫」並提供「註冊版本」secondary CTA |
| Error | 顯示 `error.code` 與「重新載入」；不可留下看似可複製的舊 URL |
| Partial / Streaming | Agent 詳情已到、版本仍 `BUILDING` 時顯示 API 回傳 URL，但標「尚不可呼叫」；Copy 可用 |
| Success | 三個 URL 全部顯示且可複製；缺少任一欄即視為 Error，不能只渲染兩個 |

### 5.6 `create-agent-dialog.js` — 建立 Agent metadata

`/console/agents` 頂部唯一 primary CTA「建立 Agent」開啟原生 `<dialog>`；不新增 route，也不設計多步精靈。
表單只有 `name`（必填）與 `description`（選填）。送 `POST /v1/agents`，`201` 後導向
`/console/agents/{agent_id}`，讓使用者接著註冊第一個 image 版本。

| 狀態 | 呈現與動作 |
|---|---|
| Loading | 送出後兩欄 disabled，按鈕文字「建立中」；保留輸入值 |
| Empty | 初始空表單；name 未填時 primary CTA disabled，取消回到列表 |
| Error | 顯示 `error.code`、訊息與欄位 `details`；保留輸入，focus 第一個錯誤欄 |
| Partial / Streaming | name 已填而 description 未填是合法 partial；顯示下一步說明「建立後註冊 image 版本」 |
| Success | 關閉 dialog 並導向新 Agent 詳情；不在列表頁複製成功 toast 與 redirect 兩套結果 |

### 5.7 `agent-list.js` — Agent 列表與選擇入口

列表列顯示 `name`、截斷 description、`latest_published_version`、`updated_at`；整列標題連到 Agent 詳情。
`agent_id` 可在展開的次要資訊中完整複製。最新版本為 `null` 時顯示文字「尚無已發布版本」，不可顯示假狀態。

| 狀態 | 呈現與動作 |
|---|---|
| Loading | 表頭與 5 列骨架；不顯示假資料 |
| Empty | 「建立第一個 Agent，接著註冊 OCI image 版本」+「建立 Agent」secondary CTA |
| Error | 顯示 `error.code`、訊息與「重新載入」；頁首建立 CTA 仍可用 |
| Partial / Streaming | 已有 items、載入下一 cursor 時保留現有列，底部顯示一列骨架 |
| Success | 顯示 items；next cursor 非 null 時提供「載入更多」，不做無限捲動 |

### 5.8 `agent-version-list.js` — 版本驗證與 artifact 摘要

Agent 詳情的版本列顯示 `semver`、§2.1 狀態、`version_id` 短碼、platform、size 與 `created_at`。
展開列才顯示完整 `artifact.image_ref`、digest、runtime contract、entrypoints 與失敗資訊；Copy 使用完整值。
列表頂部 secondary CTA「註冊版本」連至既有 version-new route。

| 狀態 | 呈現與動作 |
|---|---|
| Loading | 固定列骨架 |
| Empty | 「尚未註冊版本」+「註冊第一個版本」secondary CTA |
| Error | 列表請求錯誤顯示 `error.code` 與重載；單一 FAILED version 依 §2.1 正常呈現，不算頁面 Error |
| Partial / Streaming | 任一版本為 `BUILDING` 時每 2 秒輪詢該版本；已發布與失敗列保持可操作，離頁清除全部 interval |
| Success | 所有版本依 created time 新到舊顯示；PUBLISHED 列可帶入呼叫區，FAILED 列可展開除錯 |

### 5.9 `run-composer.js` — 選版本、entrypoint 並建立 Run

位於 Agent 詳情，寬度 `--reading-max`。使用者依序選擇 `PUBLISHED` 版本、entrypoint，再輸入 JSON object。
版本預設 `latest_published_version`；切換版本後讀取該版本詳情並更新 entrypoint 與 schema 說明。
為控制原生 JS 複雜度，MVP 使用 mono JSON textarea，不把任意 JSON Schema 轉成動態表單。

- 版本選單只列 `PUBLISHED`；`BUILDING` / `FAILED` 不可選。
- entrypoint 選單顯示 name + description；下方 `<details>` 可展開唯讀 `input_schema`。
- input 必須先能 parse 成 JSON object；前端可依 schema 提示，後端 `POST /v1/agents/{agent_id}/runs` 仍做權威驗證。
- 提交 body 只含 `version`、`entrypoint`、`input`；呼叫端不可傳 `trigger`、`image_ref`、command、port、network 或 volume。
- Console 不送 `X-Zentra-Trigger` 或其他 trigger discriminator。server 會把這個 REST Run 記為 `API`；
  `trigger` 是回應中的唯讀接入協定，不是 UI 身分。
- 收到 `202` 後立即導向 `/console/runs/{run_id}`，由既有 Run 觀測頁接手 SSE；不得在 Agent 詳情另開第二套事件流。

| 狀態 | 呈現與動作 |
|---|---|
| Loading | 載入版本／entrypoints 時 select 與 textarea disabled，顯示欄位骨架 |
| Empty | 沒有 PUBLISHED 版本時說明「先完成 image 驗證」並提供「註冊版本」secondary CTA；沒有 entrypoint 視為契約錯誤 |
| Error | JSON parse 錯誤就地顯示；API 錯誤顯示 `error.code`、訊息與欄位 details；若已有 `run_id` 必須顯示並可前往觀測 |
| Partial / Streaming | version 已選但版本詳情仍在載入時保留選擇；送出中鎖定欄位並顯示「建立 Run 中」，禁止重送 |
| Success | `202` 回應後導向既有 Run 觀測頁；Run 終態與串流只由該頁顯示 |

### 5.10 `recent-runs.js` — Agent 最近 Runs

以 `GET /v1/runs?agent_id={agent_id}&limit=…` 顯示 status、agent version、trigger 與 created time；
列連到 `/console/runs/{run_id}`。只顯示精簡 Run，不在 Agent 詳情重複 input/output。
trigger 只接受 `API` 與 `MCP`：Console 建立的 Run 顯示 `API`，不得顯示或保留 `CONSOLE` 分支。

| 狀態 | 呈現與動作 |
|---|---|
| Loading | 固定列骨架 |
| Empty | 「此 Agent 還沒有執行紀錄」；有 PUBLISHED version 時 CTA 聚焦 Run composer，否則導向註冊版本 |
| Error | 顯示 `error.code` 與重新載入；不影響版本與 Run composer |
| Partial / Streaming | 載入下一 cursor 時保留現有列；RUNNING 列只顯示當前已知狀態，不在此頁開 SSE |
| Success | 依 created time 新到舊顯示；有 next cursor 時提供「查看全部 Runs」連至 Runs 頁並帶 Agent 篩選 |

### 5.11 按鈕（CSS class，不是元件）

| class | 樣式 |
|---|---|
| `.btn-primary` | `--accent` 底、`--bg-base` 文字。**全頁最多一個** |
| `.btn-secondary` | `--surface-3` 底、`--border-default` 邊、`--text-primary` 文字 |
| `.btn-ghost` | 透明底，hover `--surface-hover` |
| `.btn-danger` | 透明底、`--danger` 文字與邊框；hover 才填 `--danger` |

高度 `--control-md`（CTA 用 `--control-lg`），左右內距 `--space-4`，圓角 `--radius-md`。

### 5.12 五頁組合與完整操作流程

1. `/console/agents`：載入 `agent-list.js`；頁首「建立 Agent」開 `create-agent-dialog.js`。
2. 建立成功導向 `/console/agents/{agent_id}`。若尚無發布版本，頁面首先顯示註冊版本 CTA。
3. `/console/agents/{agent_id}/versions/new`：`version-registration.js` 接收 semver + canonical image ref，
   確認後註冊並輪詢終態；PUBLISHED 後回 Agent 詳情。
4. Agent 詳情由 `agent-version-list.js`、`endpoints-card.js`、`run-composer.js` 與 `recent-runs.js` 組成。
   首屏順序是 metadata → 版本 → 呼叫區 → endpoints → 最近 Runs；呼叫區只在有 PUBLISHED version 時可用。
5. Run 建立成功導向 `/console/runs/{run_id}`，沿用 `streaming-output.js` 與 `run-timeline.js`；
   `/console/runs` 仍提供跨 Agent 查找與篩選。

同一頁最多一個 primary CTA：Agent 列表是「建立 Agent」、version-new 是「註冊並驗證」、Agent 詳情有可呼叫
版本時是「執行 Agent」。其他動作用 secondary / ghost，避免 metadata、發布與執行三種操作互相競爭。

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
        streaming-output.js  run-timeline.js  endpoints-card.js
        create-agent-dialog.js  agent-list.js  agent-version-list.js
        version-registration.js  run-composer.js  recent-runs.js
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
const jsonPost = (body) => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});
export const listRuns   = (q)  => req('/runs?' + new URLSearchParams(q));
export const getRun     = (id) => req(`/runs/${id}`);
export const cancelRun  = (id) => req(`/runs/${id}/cancel`, { method: 'POST' });
export const listAgents = (q)  => req('/agents?' + new URLSearchParams(q));
export const createAgent = (body) => req('/agents', jsonPost(body));
export const getAgent = (id) => req(`/agents/${id}`);
export const listAgentVersions = (id) => req(`/agents/${id}/versions`);
export const getAgentVersion = (id, semver) => req(`/agents/${id}/versions/${encodeURIComponent(semver)}`);
export const registerAgentVersion = (id, body) => req(`/agents/${id}/versions`, jsonPost(body));
export const createRun = (id, body) => req(`/agents/${id}/runs`, jsonPost(body));
```

沒有型別檢查（ADR-0006 的已知代價），**所以這層的紀律是唯一的防線**。
API 原始欄位的讀取與正規化集中在這裡；頁面與元件只使用 `api.js` 回傳的 view model。
`jsonPost()` 必須固定帶 `Content-Type: application/json`。`image_ref` 只能出現在版本註冊 request，
不能出現在 Run request。`createRun()` 不得在 body 加 `trigger`，也不得送 `X-Zentra-Trigger`；trigger 完全由 server 決定。

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
| v0.5.0 | 2026-09-22 | 依 ADR-0008 與 ZEN-008 把 `.tar.gz` 上傳改為 canonical OCI digest 註冊；在既有五頁內新增 Agent metadata dialog、版本驗證狀態、artifact 摘要與 Run composer，定義每個新增元件的 Loading / Empty / Error / Partial / Success 五態；API 欄位對齊 `api-contract.md v0.3.0` | @ui-designer |
| v0.5.1 | 2026-09-22 | 對齊 SPEC / API v0.3.1：Console Run request 不送 body/header trigger discriminator；server 將 Console 與其他 REST 呼叫記為 `API`，MCP 記為 `MCP`；可見流程不變 | @ui-designer |
