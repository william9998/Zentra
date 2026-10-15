# UI 契約 (UI Contract)

| 欄位 | 值 |
|---|---|
| 契約版本 | `v0.12.0` |
| 擁有者 | `@architect` |
| 對齊 Spec | `specs/SPEC.md v0.7.0` |
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
> | 1 | `/console/agents` | `agents.html` | Agent 總覽（卡片格子，§5.7）；以頁內 `<dialog>` 建立 Agent metadata |
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

### 1.1 色彩（深色為預設，v0.7.0 起可切換淺色）

```css
:root {
  color-scheme: dark;

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

/* 淺色模式（v0.7.0 新增）。**只覆寫顏色**，字級、間距、幾何一律共用 ——
   那些跟明暗無關，複製一份只會多一個會漂掉的地方。

   為什麼疊加值要換成黑：深色的表面靠「疊白」提亮，淺色底疊白等於什麼都沒做。
   為什麼狀態色要加深：深色版刻意降過飽和度以貼合近黑底，那些值放到白底上
   對比不足，會變成「看得到但讀不出來」。 */
:root[data-theme="light"] {
  color-scheme: light;

  /* 底層 */
  --bg-base:          #fbfbfb;
  --bg-sidebar:       #f3f3f3;
  --bg-elevated:      #ffffff;
  --bg-code:          #f1f1f1;

  /* 表面（疊加式）—— 淺色疊黑，不是疊白 */
  --surface-1:        rgba(0, 0, 0, 0.02);
  --surface-2:        rgba(0, 0, 0, 0.03);
  --surface-3:        rgba(0, 0, 0, 0.04);
  --surface-hover:    rgba(0, 0, 0, 0.05);
  --surface-active:   rgba(0, 0, 0, 0.08);

  /* 邊框 */
  --border-subtle:    rgba(0, 0, 0, 0.07);
  --border-default:   #dcdcdc;
  --border-strong:    #c0c0c0;
  --border-focus:     #4a4a4a;

  /* 文字 */
  --text-primary:     #1f1f1f;
  --text-secondary:   #6b6b6b;
  --text-disabled:    #a6a6a6;

  /* 主色 —— 與深色互為反相：`.btn-primary` 的字用 --bg-base，兩邊都會有對比 */
  --accent:           #3a3a3a;
  --accent-hover:     #1f1f1f;
  --accent-subtle:    rgba(58, 58, 58, 0.08);

  /* 狀態色 —— 加深以貼合淺底 */
  --status-queued:     #8a8a8a;
  --status-scheduling: #6b6b6b;
  --status-running:    #2b6cb0;
  --status-waiting:    #8a6516;
  --status-succeeded:  #2c6b4f;
  --status-failed:     #b03a34;
  --status-cancelled:  #9a9a9a;

  --danger:  #b03a34;
  --warning: #8a6516;
  --success: #2c6b4f;
}
```

> **只有 §2 的狀態指示器可以用狀態色。** 其他地方一律單色。

> **淺色是覆寫，不是第二套系統。** 只有 `:root[data-theme="light"]` 這一個區塊，
> 而且只放顏色。新增顏色 token 時**兩邊都要加** —— 只加深色的話，它在淺色模式下
> 會保持深色，而那在畫面上看起來像一塊莫名其妙的暗色，不像少了設定。
> `test_light_theme_covers_every_colour_token` 就是擋這件事的。
>
> `color-scheme` 一定要跟著設：它決定原生元件（`select`、`checkbox`、捲軸）的外觀。
> 少了它，淺色模式的下拉選單會是深色的。

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
--content-max:     1200px;   /* 列表、表格、卡片格子 */
--reading-max:     768px;    /* 表單、文件、LLM 輸出 */
--card-min-width:  280px;    /* 卡片格子單欄的最小寬度（v0.11.0，§5.7） */
--monogram-size:   40px;     /* 名稱首字方塊的邊長（v0.11.0，§5.7） */
--row-height:      44px;     /* 列表列 */
--row-height-compact: 36px;  /* 事件流、log */
--header-height:   52px;

--transition: 0.2s;
```

**比例規則（避免版面失衡）**：
- 卡片內距 `--space-4`，卡片之間 `--space-3`
- 卡片格子的欄數由寬度決定，不寫斷點：每欄不窄於 `--card-min-width`，容器比它還窄時退成一欄滿寬（§5.7）
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
| 切到淺色（v0.9.0） | `Sun` | `i-sun` |
| 切到深色（v0.9.0） | `Moon` | `i-moon` |

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

**色彩模式切換（v0.7.0 新增）。** 頂部列最右邊一顆 `.btn-ghost`，只改
`<html data-theme>`，不碰任何元件 —— 顏色全是 token，所以切換是一個屬性的事。

1. 按鈕是**只有圖示**的方形鈕（v0.9.0 起，原本是文字），圖示說的是**按下去會變成什麼**：
   深色時顯示 `i-sun`，淺色時顯示 `i-moon`；現在是什麼畫面本身已經在說了。
   沒有可見文字，所以 `aria-label` 與 `title` 都要寫完整句（「切換為淺色模式」）。
2. 沒存過偏好就跟隨作業系統（`prefers-color-scheme`），存過就以使用者的選擇為準。
3. 偏好放 `localStorage`，與側欄摺疊同類。§6.4 禁的是**憑證**，不是偏好。
4. **不使用內嵌 `<script>` 消除初次載入的閃動** —— HTML 不准有內嵌 script（§6.3）。
   代價誠實講：偏好與系統設定不同時，首次繪製會有一瞬間的舊色。Console 的內容
   本來就是 JS 繪製的，閃的只有底色。

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
- **靜態說明區塊**（ZEN-009 / ADR-0009 R2）：表單上方一個 `<details>`，摘要「怎麼把 image 推上來」，
  展開後是四步流程的**純文字**指令（`docker tag` → `docker push` → 取 digest → 貼進下面的欄位），
  外加兩句提醒：「**只能在跑 Zentra 的那台主機上執行**」與「`localhost:6000` 是平台內部的 Registry 位址，
  不是你的筆電」。預設收合。
  **沒有新元件狀態、沒有檔案輸入、沒有拖放區、沒有進度條、沒有 `XMLHttpRequest`** ——
  這是靜態文字，不影響上面的五態。指令用 `<pre>`，內容一律 `textContent`。
- 點「註冊並驗證」先開原生 `<dialog>`，逐行顯示 Agent 名稱、`semver`、完整 `image_ref`，並說明
  「如果這個版號已經存在，驗證通過後會取代現有的 image，舊 image 會被刪除」（ADR-0016）。只有 dialog 的確認按鈕送出 JSON `POST /v1/agents/{id}/versions`。
- 收到 `202` 後鎖定欄位，每 2 秒輪詢版本詳情。`destroy()` 必須 `clearInterval`；終態也要立即停止輪詢。
- `BUILDING` 顯示聚合進度、已耗時與三個固定步驟說明「拉取 exact digest → inspect runtime → 隔離驗證」，
  但不可把其中任何一步標成目前 stage，因 API 未提供即時 stage。
- `PUBLISHED` 顯示完整 `artifact.digest`、`platform`、格式化後 size、`runtime_contract` 與 entrypoint 數量；
  primary CTA「前往 Agent 詳情」。Copy 一律複製完整值，不能複製短碼。
- `FAILED` 顯示 `BUILD_FAILED`、`error.message` 全文、`details.stage`、`agent_version_id` 與完整 image ref，
  並提供 Copy。`BUILD_FAILED` 不可重試原請求；CTA 是「返回 Agent 詳情」與「修正後重新註冊」（同版號可以再送）。
- `409 VERSION_CONFLICT` 明示這個版號正在驗證中、等它完成再送；`422 VALIDATION_ERROR` 將
  `details` 對應到欄位。不得自創其他錯誤碼。

五態：

| 狀態 | 呈現與動作 |
|---|---|
| Loading | 初次載入 Agent metadata 時以表單骨架顯示；送出中按鈕改「註冊中」並 disabled，避免重送 |
| Empty | `semver` 與 `image_ref` 空白表單；主 CTA disabled，說明先 push 再貼 canonical digest |
| Error | 顯示 API `error.code`、訊息與可行動建議；若已建立版本則同時顯示 `agent_version_id` |
| Partial / Streaming | `BUILDING` 輪詢態；保留 `202` 已知 metadata，超過 3 秒顯示驗證工作內容與已耗時 |
| Success | `PUBLISHED` 摘要與前往詳情 CTA；輪詢已停止 |

### 5.7 `run-graph.js` — Run 的流程圖版面（v0.5.4 新增，ZEN-014）

Run 觀測頁的第二種版面。時間軸上方一組 `role="group"` 的切換鈕（時間軸 / 流程圖），
用 `aria-pressed` 表示目前版面，**不是**只靠顏色。

**畫的是執行軌跡，不是 graph 定義。** 平台不知道使用者的 graph 長什麼樣 ——
`entrypoints` 只描述輸入輸出。手上只有 `node.start` / `node.end` 的先後順序，
所以畫的是「這次實際走過的路」：

- 節點依**第一次出現**的順序**由左而右**單列橫排；跑過多次的標 `×N`，不重複畫框
- 往回走的邊（回到更早出現過的節點）畫成**上方**弧線，**虛線 + 不同顏色** —— 那是 retry 迴圈。
  跨得越遠拱得越高，多條弧線才不會疊在一起
- 每個節點一個狀態圓點：執行中 / 已完成，配合框線顏色，不只靠顏色區分

**硬性規則**：

1. **手寫 inline SVG，不得引入任何繪圖函式庫**（d3 / cytoscape / mermaid 一律不行，ADR-0006）。
   版面因此刻意選單列橫排：座標是 index 乘間距，不需要佈局演算法。
2. **SVG 用自然像素寬度，不是 `width="100%"`。** 設成 100% 會把長流程縮到看不清字；
   給真實寬度、由外層容器橫向捲動，節點大小才固定。容器右側留漸層暗示還有內容。
3. 節點名稱來自使用者的程式碼，是**不可信字串** —— 一律 `textContent`，
   過長截斷並把完整名稱放 `<title>`。
4. **不自己訂閱 SSE。** 兩個版面共用時間軸收到的同一份事件；
   兩份訂閱會各自漏事件，而且沒人分得出哪一份是對的。
5. Run 進終態時要收到 `finished`，否則沒收到 `node.end` 的節點會永遠顯示成執行中。

五態：

| 狀態 | 呈現與動作 |
|---|---|
| Loading | 不適用（資料來自時間軸，切過去時已經有了） |
| Empty | 還沒有節點事件時顯示一句說明，不畫空白畫布 |
| Error | 不適用（錯誤由時間軸與頁面錯誤區負責） |
| Partial / Streaming | 執行中的節點以 `--running` 框線與圓點標示；新事件進來即重繪 |
| Success | 全部節點 `--done`；圖例說明有幾個節點、幾個跑了不只一次、有沒有回頭的邊 |

### 5.6 `llm-credential-card.js` — 這個 Agent 的 LLM 憑證（v0.5.3 新增，ZEN-012）

位於 Agent 詳情頁的一張 card。**不在版本列表裡** —— 憑證綁 Agent 不綁版本
（[ADR-0011](./adr/ADR-0011-per-agent-llm-credentials.md)），畫面必須說出這件事，
那是使用者最容易誤會的地方。

- 卡片上方一句話說明「憑證屬於這個 Agent，不屬於某個版本；更換則所有版本立刻一起變」。
- 摘要列：未設定時說「會用平台預設的金鑰」；已設定時顯示狀態圓點、**尾四碼**、
  `base_url`、`default_model` 與更新時間。**全部 `textContent`**。
- `api_key`：`type="password"`，必填，少於 8 字元時 primary CTA 維持 disabled。
  欄位說明要寫「寫進去之後就讀不出來」。**送出成功後立刻清空欄位**，不留在 DOM 裡。
- `base_url` / `default_model`：選填，label 帶 `.field-optional` 標記。
- 已設定時 primary CTA 改為「更換憑證」，並多一個「移除憑證」的 `btn-ghost`。
- **兩個破壞性動作都要原生 `<dialog>` 二次確認**，且確認文案要講出後果：
  更換 =「立刻對所有版本生效，沒有灰度也沒有回滾」；
  移除 =「退回用平台預設金鑰，不是停止呼叫 LLM」。
- 錯誤提示要點出「平台若沒設定 `ZENTRA_SECRET_KEY` 就不接受憑證，它不會退而存明文」。
- 屬性名一律 `data-cred-*` 前綴：這張卡與編輯／刪除對話框同頁，
  `data-form` / `data-submit` / `data-summary` 已經被它們用掉了。

**沒有「顯示金鑰」的功能，而且不該假裝有** —— 契約保證回應只有尾四碼（api-contract §2.1）。

五態：

| 狀態 | 呈現與動作 |
|---|---|
| Loading | 摘要顯示「正在讀取憑證狀態…」，表單維持 hidden |
| Empty | 未設定：摘要說明會用平台預設金鑰；只有「儲存憑證」一個 CTA |
| Error | 讀取或寫入失敗顯示 `error.code` 與可行動建議；金鑰欄位標 `aria-invalid` |
| Partial / Streaming | 不適用（沒有串流） |
| Success | 摘要顯示尾四碼與更新時間；`aria-live` 宣告「所有版本都會用它」 |

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
| Empty | 初始空表單；name 未填時 primary CTA disabled，取消回到總覽 |
| Error | 顯示 `error.code`、訊息與欄位 `details`；保留輸入，focus 第一個錯誤欄 |
| Partial / Streaming | name 已填而 description 未填是合法 partial；顯示下一步說明「建立後註冊 image 版本」 |
| Success | 關閉 dialog 並導向新 Agent 詳情；不在總覽頁複製成功 toast 與 redirect 兩套結果 |

### 5.7 `agent-list.js` — Agent 總覽（卡片格子）與選擇入口

**v0.11.0 起由「一列一列的清單」改成卡片格子，直接取代，不提供卡片／清單切換**（多一種版面就多一套要測、要維護的
DOM 與五態）。每個 Agent 一張卡，整張卡點下去進入 `/console/agents/{agent_id}`。資料來源不變：仍是
`GET /v1/agents` 的 items 與 cursor，**不新增任何 API 欄位**。

**卡片內容（由上而下）**

| 區塊 | 內容與規則 |
|---|---|
| 名稱首字方塊 | 取 `name` 的第一個字母或數字（`\p{L}` / `\p{N}`，以 code point 計，拉丁字母轉大寫）；整個 name 都沒有字母數字時顯示 `—`。**不用 emoji、不配色**（§0）。方塊 `--monogram-size` 正方形、`--surface-3` 底、1px `--border-default`、`--radius-md`、`--font-display` `--text-lg` `--text-secondary`，文字置中。純裝飾，`aria-hidden="true"` |
| 名稱 | `--text-md` `--weight-medium` `--text-primary`，單行，超出以省略號截斷；完整名稱放 `title` |
| 描述 | `--text-sm` `--text-secondary`，**最多兩行**，超出截斷（`line-clamp: 2`）。保留兩行高（`calc(2 * var(--text-sm) * var(--leading-normal))`），讓同一列的卡片底部對齊。`null` 或空字串時顯示「沒有描述」 |
| 最新版本 | `latest_published_version`，`--font-mono` `--text-xs`。`null` 時顯示「尚無已發布版本」（`--text-secondary`、非 mono），**不可顯示假狀態** |
| 更新時間 | `updated_at`，`--text-xs` `--text-secondary`，格式沿用 `formatDateTime` |

**卡片上不放 Agent ID。** 完整 `agent_id` 與它的 Copy 鈕在 Agent 詳情頁的 metadata 區（§5.12 第 4 點，現行
`pages/agent.js` 已有「Agent ID + Copy」）。卡片上若另放一顆複製鈕，等於在「整張卡可點」的區域裡再塞一個
互動元素 —— 點偏一點就從「複製」變成「離開頁面」。要複製 ID 的人多走一步進詳情頁，代價可接受。

**DOM 與無障礙語意（§7）**

- 每張卡是一個 `<article class="agent-card">`（`position: relative`），名稱是 `<h3>` 裡的 `<a href="/console/agents/{agent_id}">`。
  **一般模式下這個 `<a>` 是整張卡唯一的互動元素。**
- 整張卡可點靠 CSS 擴大點擊範圍：`a::after { content: ""; position: absolute; inset: 0; }`。
  **不准把整張卡包進 `<a>`**，也不准在 `<article>` 上掛 click handler 假裝連結 —— 前者會讓螢幕報讀器把
  整張卡的文字念成一個超長連結，後者鍵盤進不去。
- 鍵盤：Tab 依序停在每張卡的名稱連結，Enter 導頁。focus 樣式畫在擴大後的整張卡上：
  `a:focus-visible::after` 用 2px `--border-focus` outline + 2px offset、圓角 `--radius-lg`；
  連結本身不另畫 outline（避免一張卡出現兩個 focus 框）。
- 首字方塊 `aria-hidden="true"`；描述被截斷的部分仍在 DOM 中，報讀器念得到全文。

**卡片外觀與 hover / focus**

- 卡片：`--surface-2` 底、1px `--border-subtle`、`--radius-lg`、內距 `--space-4`，內部元素間距 `--space-3`；
  內部用 flex 直排，版本與更新時間那一行 `margin-top: auto` 貼底。**不准陰影、漸層、彩色**。
- hover（一般模式）：底色換 `--surface-hover`、邊框換 `--border-default`。
  只有明度疊加與髮絲邊框變化，不位移、不放大、不加陰影。
- `transition: background-color var(--transition), border-color var(--transition)`；
  `prefers-reduced-motion: reduce` 時取消 transition。

**格子版面（RWD）**

- 容器 `display: grid`，`grid-template-columns: repeat(auto-fill, minmax(min(var(--card-min-width), 100%), 1fr))`，
  欄距與列距都是 `--space-3`，寬度上限 `--content-max`。欄數隨寬度自適應，**不寫 media query 斷點**；
  容器窄於 `--card-min-width` 時 `min(…, 100%)` 讓卡片縮成一欄滿寬（手機一欄）。
  頁面左右留白依 §1.3：桌面 `--space-6`、行動裝置 `--space-4`。
- **格子不放在 `.card` 面板裡**（卡片套卡片會出現兩層表面與兩種圓角）。工具列、格子、頁尾「載入更多」
  直接排在頁面內容區，彼此間距 `--space-4`。

**工具列**

- 一般模式：搜尋框（`<input type="search">`，有可見或 visually-hidden 的 `<label>`「搜尋 Agent」，
  placeholder「搜尋名稱或描述」，`--control-lg`）＋右側 `.btn-secondary`「選取」。
  搜尋沿用現行行為：只在已載入的 items 中比對 `name` 與 `description`，不呼叫 API。
- 選取模式：搜尋框保留可用；「選取」換成 `.btn-ghost`「取消」，並顯示
  「全選」checkbox（`<label>` 文字「全選目前顯示的」）、已選數量「已選 N 個」（`aria-live="polite"`）、
  `.btn-danger`「刪除所選」（N = 0 時 disabled）。
- 頁首「建立 Agent」仍是全頁唯一的 primary（§5.12），選取模式不改變它。

| 狀態 | 呈現與動作 |
|---|---|
| Loading | 工具列照常顯示（搜尋框與「選取」disabled），格子內 6 張**骨架卡**：與真卡同尺寸，首字方塊、名稱、兩行描述、底行各一條 `--surface-2` 微光條。**不顯示假資料**（不放假名稱、假版本號） |
| Empty | 沒有任何 Agent：格子位置改成置中說明「建立第一個 Agent，接著註冊 OCI image 版本」+「建立 Agent」secondary CTA；「選取」disabled。搜尋後零筆（但有 items）：顯示「沒有符合搜尋條件的 Agent。」，不給 CTA |
| Error | 顯示 `error.code`（mono）、訊息與「重新載入」；頁首建立 CTA 仍可用；不留下看似可點的舊卡片 |
| Partial / Streaming | 已有 items、載入下一 cursor 時保留現有卡片，格子末端追加一排骨架卡（數量 = 目前欄數，至少 1 張）；「載入更多」改「載入中」並 disabled |
| Success | 顯示卡片格子；next cursor 非 null 時格子下方置中提供 `.btn-secondary`「載入更多」，**不做無限捲動** |

**批次選取與刪除（v0.8.0 新增，ADR-0013；v0.11.0 改為「選取模式」）。** 勾選框平常**不顯示**。
按工具列「選取」進入選取模式後，每張卡左上角才出現 checkbox；按「取消」離開選取模式並**清空選取**。

- 選取模式下，名稱不再是 `<a>`：每張卡的唯一互動元素換成 `<input type="checkbox">`，它的 `<label>` 包住
  名稱並以同樣的 `::after { position: absolute; inset: 0; }` 擴大到整張卡 —— **點卡片任何地方 = 切換勾選，不導頁**，
  這是原生 label 行為，不另寫 click handler。checkbox 的可及名稱就是 Agent 名稱。
  Tab 停在 checkbox，Space 切換；focus 樣式同一般模式（畫在整張卡上）。
- checkbox 與 label 不准包進任何 `<a>`；選取模式下卡片內不存在連結，也就不會有「點到連結還是點到勾選」的歧義。
- 已勾選的卡：底色 `--surface-active`、邊框 `--border-strong`，加上 checkbox 本身的勾選狀態
  （**不只靠顏色**，§0 第 4 條）。checkbox 位置在卡片內距內的左上角，首字方塊往右讓位，不與內容重疊。
- 按「取消」離開選取模式或重新載入時，選取一律清空；刪除完成後只移除刪掉的，失敗的保留勾選（見第 4 條）。

以下四條規則照舊，換成卡片後一條都不放寬：

1. **全選只作用在目前看得到的那些。** 搜尋過濾之後還去勾看不見的卡，
   是那種按下去才發現刪多了的錯。部分選取時「全選」checkbox 用 `indeterminate`。
2. **選取記的是 `agentId`，不是卡片的索引或位置。** 搜尋會換掉可見的卡，欄數也會隨寬度變，記位置會刪到別人。
   搜尋前已勾、搜尋後被藏起來的 Agent 仍算已選，所以第 3 條的確認視窗要列出**全部**已選的名字。
3. **確認視窗要列出名字，並講明代價**：不可復原，連 Run 紀錄、用量與 registry 裡的
   image 一起刪（api-contract §2.5）。只說「確定刪除嗎」不夠 —— 使用者不會知道
   自己同時放棄了稽核軌跡。確認視窗用原生 `<dialog>`。
4. **一個失敗不要停下來。** 逐一呼叫 `DELETE`，全部試完再一起回報哪些沒刪掉；
   停在中間會留下「刪了一半」而使用者無從得知刪到哪裡。刪除成功的卡從格子移除，失敗的保留並維持勾選。

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

### 5.13 `api-keys-card.js` — 下游 API key（v0.6.0 新增，ZEN-015）

在 Agent 詳情頁，緊鄰 §5.6 的憑證卡。**兩張卡管的是相反方向的東西**，
放在一起是為了讓這件事一眼看得出來：憑證是這個 Agent 往外打 LLM 用的，
API key 是別人往裡打這個 Agent 用的。

1. **明文只出現一次。** 發完之後用一個必須自己關掉的區塊顯示，附複製鈕，
   並明說「關掉就沒了」。不准做「再看一次」——平台存的是雜湊，那個功能不存在。
2. **撤銷過的 key 留在清單裡**（灰掉，`data-row--muted`），因為它的歷史用量還掛著。
   狀態用 §2 的狀態圓點 + 文字，**不只靠顏色**。
3. 空狀態要說清楚代價：「還沒有發過 key，所有呼叫都記成未歸戶」。
4. 卡片把 key 清單透過 `onChange` 推給 §5.14 的面板，**兩邊不各抓一次** ——
   不然下拉選單會跟清單不同步。
5. Console 是同源呼叫，**不自己組認證標頭**（§6.4）。怎麼帶 key 的說明留給契約 §2.3。

### 5.14 `usage-panel.js` — 用量與除錯（v0.6.0 新增，ZEN-015）

回答兩個問題，所以是上下兩半：上半是總計 + 依 key + 依 model 的彙總，
下半是逐筆 LLM 呼叫。篩選列有 key 下拉、「只看出錯的」勾選與重新整理。

1. **`key_id` 為 null 的那一列要顯示成「未歸戶」**，並說明是 Console 發起的。
   濾掉它總計就對不起來。
2. **金額不做運算。** `costUsd` 是十進位字串，直接印。要合計讓後端 SQL 算。
3. 逐筆表的每一列都連到那個 Run —— 查到哪筆錯之後，下一個動作一定是去看它。
4. 逐筆表用游標翻頁（「載入更多」），不是一次抓全部。

### 5.15 `containers-card.js` — 常駐容器（v0.6.1 新增，ZEN-015）

在 Agent 詳情頁。列出這個 Agent 的常駐容器，可預熱／重啟／停止。

1. **文案要說清楚容器會自己起、也會自己收。** 按鈕叫「預熱」不叫「啟動」——
   叫啟動會讓人以為不按就不能用。空狀態要明說「沒有容器不是故障」，
   否則每次回來看到空的都會以為出事。
2. **`restartCount` 一定要顯示**（§2.2）。一直重啟的容器是壞掉了，
   不是需要再重啟一次 —— 少了這個數字，那件事看起來跟「剛啟動」一模一樣。
3. 狀態用 §2 的圓點 + 文字，**不只靠顏色**。五個狀態借用既有的圓點 class，
   不自創（`STARTING→SCHEDULING`、`READY→SUCCEEDED`、`STOPPED→CANCELLED`…）。
4. **只有在有容器處於 `STARTING` 時才輪詢**，其餘時間這張卡完全不發請求。
5. 容器綁單一版本（P5），發新版會並存兩個，所以這裡是列表不是單筆。

### 5.16 `agent-config-card.js` — 這個 Agent 的可變設定（v0.10.0 新增，ZEN-019）

在 Agent 詳情頁。編輯 api-contract §2.6 的那份 JSON 文件。

1. **第一句話就要說「這裡不是放密碼的地方」**，並指向下面那張機密卡。
   這是整個 ZEN-019 最容易出事的誤用：使用者把 API key 貼進設定，
   它會明文顯示在畫面上、明文送進沙盒，而且沒有任何東西會警告他。
   **平台不掃描內容**（猜不準的警告比沒有警告更糟），所以這句文案就是唯一的防線。
2. **編輯器是一個 `<textarea>` 裝 JSON**，不是動態鍵值表單。
   理由：值可以是巢狀物件與陣列（契約允許任何 JSON），鍵值表單做不到那個自由度，
   做半套會讓使用者以為不能用陣列。**在前端就驗 `JSON.parse`**，
   錯誤指出行號，不要送出去換一個 `422` 回來。
3. **送出是整份取代**（沒有 PATCH）。CTA 文案要寫「取代整份設定」，
   確認視窗要說「這份文件會完整覆蓋現有設定」—— 使用者最容易以為是合併。
4. 摘要列顯示鍵數量與 `updated_at`。**未設定時顯示 `{}` 而不是空白**，
   因為「沒設定」與「設定成空的」在契約裡是同一件事，畫面不該暗示有差別。
5. 生效範圍要寫在卡片上：「立刻對所有版本生效，下一筆 Run 就是新值；
   已經在跑的 Run 不受影響」。
6. 屬性名一律 `data-config-*` 前綴。

五態：Loading 顯示「正在讀取設定…」；Empty 是 `{}` 與一句「還沒有任何設定」；
Error 顯示 `error.code` 與可行動建議，JSON 語法錯誤時 `<textarea>` 標 `aria-invalid`；
Partial / Streaming 不適用；Success 以 `aria-live` 宣告「已取代，下一筆 Run 生效」。

### 5.17 `agent-secrets-card.js` — 這個 Agent 的機密（v0.10.0 新增，ZEN-019）

緊鄰 §5.16。**兩張卡放在一起，是為了讓「哪個放哪裡」一眼看得出來。**

1. **每一列只有名字、尾四碼、更新時間。** 沒有「顯示」按鈕，而且不該假裝有 ——
   契約保證回應不含值（api-contract §2.7）。與 §5.6 的 LLM 憑證卡同一條規則。
2. 值的輸入框 `type="password"`，**送出成功後立刻清空**，不留在 DOM 裡。
3. **卡片要說「這些值不會進到沙盒」**，並說明用法：在出口白名單或寄件者設定裡
   **以名字引用**它。不講的話，使用者會以為存了就能在 agent 程式碼裡讀到。
4. **刪除時若後端回 `409`**（還被引用，api-contract §2.7 第 6 點），
   錯誤訊息要直接列出 `details.referenced_by`，並提供跳到那張卡的連結。
   只說「刪不掉」等於要使用者自己去猜是誰在用。
5. 未設定 `ZENTRA_SECRET_KEY` 時後端回 `422`；文案要點出
   「平台沒設定加密金鑰就不接受機密，它不會退而存明文」（同 §5.6）。
6. 屬性名一律 `data-secret-*` 前綴。

### 5.18 `egress-card.js` — 對外出口與寄信（v0.10.0 新增，ZEN-019）

一張卡，上下兩塊：上面是主機白名單（api-contract §2.8），下面是寄件者（§2.9）。
合成一張是因為它們回答同一個問題：**這個 Agent 能把東西送到哪裡去。**

1. **空狀態要說出後果，不是說「沒有資料」**：「還沒有任何規則，這個 Agent 連不到任何
   外部網站」。預設拒絕是這個功能最重要的性質，而空清單看起來跟「還沒載入」一樣。
2. 每條規則顯示 host、methods（**`POST` 要用 `--warning` 色的標記標出來**，
   因為它是「能把東西寫出去」的那一個）、`path_prefix`、以及有沒有帶認證。
   **帶認證時顯示的是 secret 的名字，不是值。**
3. 新增規則的表單：`methods` 預設只勾 `GET`，勾 `POST` 時**就地顯示一行說明**
   「POST 可以把資料送到外部網站，只在需要時開啟」。
   `auth_secret_name` 是下拉，選項來自 §5.17 的清單 —— **不讓使用者手打名字**，
   打錯只會在下一次 Run 才失敗。清單為空時下拉 disabled 並指向 §5.17。
4. 寄件者區塊**沒有密碼欄位**，只有一個指向 §5.17 的 secret 下拉。
   旁邊一行說明 Gmail 要用「應用程式密碼」，不是登入密碼。
5. `allowed_recipients` 是必填的多值輸入，空的時候 CTA 維持 disabled，
   並說明理由：「沒有收件人白名單，一個迴圈寫錯就會用你的帳號寄出幾百封信」。
6. 卡片底部連到 §5.19 的稽核清單：「看看這個 Agent 實際送出去了什麼」。
7. 屬性名一律 `data-egress-*` 前綴。

### 5.19 `egress-calls-panel.js` — 出口稽核（v0.10.0 新增，ZEN-019）

與 §5.14 的用量面板並列，形狀刻意做成一樣（篩選列 + 逐筆表 + 「載入更多」）。

1. **HTTP 與寄信在同一張表**，用 `kind` 欄位分（api-contract §2.10）。
   兩個分頁會讓「這次 Run 往外送了什麼」要看兩個地方。
2. **被擋下的呼叫要顯示，而且要顯眼**：`error.details.reason` 直接譯成人話
   （`HOST_NOT_ALLOWED` → 「主機不在白名單」），並附「去加一條規則」的連結。
   這是這張表最主要的用途 —— 出口設定錯了，只有這裡看得出來。
3. 每一列連到那筆 Run（同 §5.14 第 3 點）。
4. **`path` 不含 query string**（契約 §2.10 第 1 點不入庫），
   畫面上不要暗示它是完整網址。
5. 預設篩選是「最近 50 筆」，不自動輪詢。

### 5.11 按鈕（CSS class，不是元件）

| class | 樣式 |
|---|---|
| `.btn-primary` | `--accent` 底、`--bg-base` 文字。**全頁最多一個** |
| `.btn-secondary` | `--surface-3` 底、`--border-default` 邊、`--text-primary` 文字 |
| `.btn-ghost` | 透明底，hover `--surface-hover` |
| `.btn-danger` | 透明底、`--danger` 文字與邊框；hover 才填 `--danger` |

高度 `--control-md`（CTA 用 `--control-lg`），左右內距 `--space-4`，圓角 `--radius-md`。

### 5.12 五頁組合與完整操作流程

1. `/console/agents`：載入 `agent-list.js`（Agent 總覽，卡片格子）；頁首「建立 Agent」開 `create-agent-dialog.js`；整張卡點擊進入 Agent 詳情。
2. 建立成功導向 `/console/agents/{agent_id}`。若尚無發布版本，頁面首先顯示註冊版本 CTA。
3. `/console/agents/{agent_id}/versions/new`：`version-registration.js` 接收 semver + canonical image ref，
   確認後註冊並輪詢終態；PUBLISHED 後回 Agent 詳情。
4. Agent 詳情由 `agent-version-list.js`、`endpoints-card.js`、`run-composer.js` 與 `recent-runs.js` 組成。
   首屏順序是 metadata → 版本 → 呼叫區 → endpoints → 最近 Runs；呼叫區只在有 PUBLISHED version 時可用。
   metadata 區必須顯示完整 `agent_id` 並附 Copy —— v0.11.0 起總覽卡片不放 Agent ID，這裡是 Console 上唯一能複製它的地方。
5. Run 建立成功導向 `/console/runs/{run_id}`，沿用 `streaming-output.js` 與 `run-timeline.js`；
   `/console/runs` 仍提供跨 Agent 查找與篩選。

同一頁最多一個 primary CTA：Agent 總覽是「建立 Agent」、version-new 是「註冊並驗證」、Agent 詳情有可呼叫
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
        llm-credential-card.js       ← §5.6（ZEN-012）
        run-graph.js                 ← §5.7（ZEN-014）
        api-keys-card.js             ← §5.13（ZEN-015）
        usage-panel.js               ← §5.14（ZEN-015）
        containers-card.js           ← §5.15（ZEN-015）
        agent-config-card.js         ← §5.16（ZEN-019）
        agent-secrets-card.js        ← §5.17（ZEN-019）
        egress-card.js               ← §5.18（ZEN-019）
        egress-calls-panel.js        ← §5.19（ZEN-019）
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
| v0.12.0 | 2026-10-15 | §5 註冊新版本：文案改為「同版號會取代」（ADR-0016）；`409` 的意思改為「這個版號正在驗證中」；`FAILED` 的 CTA 改「修正後重新註冊」 | 使用者裁示 |
| v0.11.0 | 2026-10-08 | **§5.7 Agent 總覽由清單改為卡片格子**（直接取代，不做切換）：卡片 = 名稱首字方塊（灰階）+ name + 兩行描述 + 最新版本 + 更新時間；整張卡以名稱 `<a>` 加 `::after` 擴大點擊範圍，不把卡片包進 `<a>`。**Agent ID 從總覽拿掉**，只留在詳情頁 metadata（§5.12 第 4 點明文要求附 Copy）。批次刪除改成「選取模式」：勾選框平常隱藏，按「選取」才出現，選取模式下點卡片 = 切換勾選；ADR-0013 四條規則不變。五態改寫成卡片版（骨架卡、Partial 追加一排骨架卡）。**新增 token**（§1.3）：`--card-min-width: 280px`、`--monogram-size: 40px`。檔頭畫面表與 §5.6 / §5.12 的「列表」用語改為「總覽」。api-contract 不動 | @architect |
| v0.10.0 | 2026-09-30 | **新增 §5.16–§5.19**（[ADR-0014](./adr/ADR-0014-per-agent-config-secrets-and-egress.md) / ZEN-019）：`agent-config-card.js`（可變設定，整份取代）、`agent-secrets-card.js`（機密，只看得到尾四碼）、`egress-card.js`（主機白名單 + 寄件者）、`egress-calls-panel.js`（出口稽核）。§6.1 檔案清單同步。**沒有新 token**。最重要的兩條文案要求：設定卡要明說「這裡不是放密碼的地方」，白名單空狀態要說「連不到任何外部網站」而不是「沒有資料」 | @architect |
| v0.9.0 | 2026-09-30 | §5.1 色彩模式切換鈕改為純圖示（`i-sun` / `i-moon`），§3 sprite 增加這兩個圖示 | @builder |
| v0.8.0 | 2026-09-29 | **§5.7 加批次選取與刪除**（ADR-0013）；Runs 列表拆成 Agent 名稱與 Agent ID 兩欄 | @architect |
| v0.7.0 | 2026-09-29 | **新增淺色模式**：§1.1 加 `:root[data-theme="light"]` 覆寫區塊（只覆寫顏色，29 個 token 全部有值）與 `color-scheme`；§5.1 加頂部列的切換鈕。深色仍是預設，沒存過偏好時跟隨作業系統 | @architect |
| v0.6.1 | 2026-09-28 | **新增 §5.15 `containers-card.js`**（ZEN-015）：常駐容器的預熱／重啟／停止與狀態。§6.1 檔案清單同步 | @architect |
| v0.6.0 | 2026-09-28 | **新增 §5.13 `api-keys-card.js` 與 §5.14 `usage-panel.js`**（ZEN-015）：下游 API key 的發放／撤銷，以及 per-key 用量與逐筆除錯。§6.1 檔案清單同步。沒有新 token | @architect |
| v0.1.0 | 2026-09-20 | 初版 | @ui-designer |
| v0.2.0 | 2026-09-20 | 改採 Stock-Insight-Chat 深色單色設計語言；**移除全部 emoji 圖示**，狀態改用圓點 + 文字；補齊尺寸/比例 token（控制項高度、圖示尺寸、版面寬度） | @ui-designer |
| v0.3.0 | 2026-09-20 | 依 ADR-0005 降維：Console 收斂成五個畫面；移除 `<AgentWizard>` 與 `<FlowCanvas>`；新增 `<VersionUpload>` 與 `<EndpointsCard>`。設計 token 與無障礙底線不變 | @ui-designer |
| v0.4.0 | 2026-09-20 | 依 [ADR-0006](./adr/ADR-0006-vanilla-frontend.md) 改用原生 HTML + CSS + ES modules（零建置）：§5 元件規格改寫成 `mount(el, props)` module；新增 §6 前端結構與實作約束（檔案配置、`api.js` 契約出口、六條硬性規則、認證）；§3 圖示改為自製 SVG sprite；RunTimeline 的虛擬捲動降級為分頁載入。**§0–§4 的設計語言、token、狀態語言、五態規範完全不變** | @ui-designer |
| v0.5.0 | 2026-09-22 | 依 ADR-0008 與 ZEN-008 把 `.tar.gz` 上傳改為 canonical OCI digest 註冊；在既有五頁內新增 Agent metadata dialog、版本驗證狀態、artifact 摘要與 Run composer，定義每個新增元件的 Loading / Empty / Error / Partial / Success 五態；API 欄位對齊 `api-contract.md v0.3.0` | @ui-designer |
| v0.5.1 | 2026-09-22 | 對齊 SPEC / API v0.3.1：Console Run request 不送 body/header trigger discriminator；server 將 Console 與其他 REST 呼叫記為 `API`，MCP 記為 `MCP`；可見流程不變 | @ui-designer |
