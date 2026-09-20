# UI 契約 (UI Contract)

| 欄位 | 值 |
|---|---|
| 契約版本 | `v0.2.0` |
| 擁有者 | `@ui-designer` |
| 對齊 Spec | `specs/SPEC.md v0.1.1` |
| 設計來源 | Stock-Insight-Chat 既有設計語言 + ChatGPT 極簡風 |

> **`@frontend-dev` 只能引用這份文件的 token，不得寫死色碼、間距、字級。**
> 要新增 token 或元件 → 開 `SPEC-QUESTION`。

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
   圖示一律用 **Lucide React 線性圖示，`stroke-width: 1.5`**，或**純色圓點**。
2. **不准用彩色漸層、陰影光暈、彩色按鈕**做裝飾。
3. **不准自創尺寸**。所有間距、字級、圓角、高度都必須引用下方 token。
4. **不准只靠顏色表達狀態** —— 每個狀態都必須同時有文字標籤。

---

## 1. 設計 Token

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
| `WAITING_INPUT` | `--status-waiting` | 等待確認 | 無 | **必須提供操作按鈕** |
| `SUCCEEDED` | `--status-succeeded` | 已完成 | 無 | — |
| `FAILED` | `--status-failed` | 失敗 | 無 | **必須顯示 `error.code`；`retryable` 時提供重試** |
| `CANCELLED` | `--status-cancelled` | 已取消 | 無 | 標籤用 `--text-disabled`，與 FAILED 明確區分 |

```
● 執行中 · llm_node · 12s          ← RUNNING
● 已完成 · 8.4s                    ← SUCCEEDED
● 失敗 · SANDBOX_TIMEOUT           ← FAILED
```

`prefers-reduced-motion` 時脈動改為靜態，用明度差異表示活躍。

---

## 3. 圖示規範

- **來源**：Lucide React，`strokeWidth={1.5}`，尺寸取 `--icon-*`
- **顏色**：繼承 `currentColor`，預設 `--text-secondary`，hover 轉 `--text-primary`
- **禁止**：emoji、彩色圖示、填充式圖示、多種圖示庫混用

常用對應（統一用法，不要各頁各自挑）：

| 用途 | Lucide |
|---|---|
| Agent | `Bot` |
| Run / 執行紀錄 | `Activity` |
| 工具 | `Wrench` |
| 知識庫 | `Library` |
| Flow 畫布 | `Workflow` |
| 設定 | `Settings` |
| 沙盒 | `Box` |
| 複製 | `Copy` |
| 展開 / 收合 | `ChevronRight` / `ChevronDown` |
| 重試 | `RotateCw` |
| 停止 | `Square` |
| 搜尋 | `Search` |

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

### 5.1 `<AppShell>`
- 左側欄 `--sidebar-width` `--bg-sidebar`，右側內容 `--bg-base`，中間 1px `--border-default`
- 側欄項目：高度 `--control-lg`，內距 `--space-3`，圓角 `--radius-md`，hover `--surface-hover`，選中 `--surface-active` + 文字轉 `--text-primary`
- 頂部列 `--header-height`，只放麵包屑與當前頁動作，**不放品牌** logo（品牌在側欄頂）
- 側欄可收合；收合後內容區左右留白補到 `--space-8`

### 5.2 `<StreamingOutput runId>`
- 內容寬度上限 `--reading-max`，行高 `--leading-relaxed`
- 逐字渲染：**用 ref 累積 + 節流 flush（60ms）**，不要每個 token 觸發 re-render
- 游標：2px 寬 `--accent`，1s 閃爍；串流結束立即移除
- 右上角常駐「停止」（`Square` 圖示 + 文字），送 `POST /v1/runs/{id}/cancel`，**不是只關本地連線**
- 斷線顯示 `--text-secondary` 的「重新連線中」一行，帶 `Last-Event-ID` 自動重連
- `aria-live="polite"`

### 5.3 `<RunTimeline runId>` — 產品最重要的元件
- 事件依時間排列，列高 `--row-height-compact`，**預設收合**，`ChevronRight` 展開
- 每列：`時間(mono, --text-2xs) · 事件類型 · 摘要`
- 工具呼叫展開後顯示 request / response，`--bg-code` 底、mono 字體、右上角 `Copy`
- 超過 200 筆啟用虛擬捲動
- 大 payload（> 2KB）折疊並顯示大小，點擊才載入
- **目標：工程師從「這次跑壞了」到「哪個節點的哪次工具呼叫回了什麼」不超過 30 秒**

### 5.4 `<AgentWizard>`
- 步驟列橫向置頂，當前步驟 `--text-primary`，其餘 `--text-disabled`，**不用數字圓圈徽章**
- 內容寬度 `--reading-max`，每步下方固定「上一步 / 下一步」
- 可回上一步且**保留已填內容**；未存草稿離開攔截（`beforeunload`）
- 發布是不可逆操作 → modal 二次確認，說明影響範圍

### 5.5 `<FlowCanvas>`
- 畫布底 `--bg-base`，格線 `rgba(255,255,255,0.03)`
- 節點：`--surface-2` + `--border-default` + `--radius-lg`，寬度固定 `220px`
- 選中：邊框轉 `--border-focus`，**不加外光暈**
- 畫布狀態與節點資料分離；存檔只存 flow DAG
- 大圖啟用 `onlyRenderVisibleElements`；編輯中未存檔攔截離開

### 5.6 按鈕
| 變體 | 樣式 |
|---|---|
| primary | `--accent` 底、`--bg-base` 文字。**全頁最多一個** |
| secondary | `--surface-3` 底、`--border-default` 邊、`--text-primary` 文字 |
| ghost | 透明底，hover `--surface-hover` |
| danger | 透明底、`--danger` 文字與邊框；hover 才填 `--danger` |

高度 `--control-md`（CTA 用 `--control-lg`），左右內距 `--space-4`，圓角 `--radius-md`。

---

## 6. 無障礙底線（不可妥協）

- 所有互動元素可鍵盤操作，focus 用 2px `--border-focus` outline + 2px offset，**清楚可見**
- 文字對比 ≥ 4.5:1。`--text-secondary` (#888) 在 `--bg-base` (#0d0d0d) 上約 5.4:1 ✓；
  **`--text-disabled` 只能用於非必要資訊**
- 狀態不能只靠顏色 —— §2 每個狀態都配了文字標籤
- 串流區塊 `aria-live="polite"`
- 尊重 `prefers-reduced-motion`

---

## 7. 變更紀錄

| 版本 | 日期 | 變更 | 由 |
|---|---|---|---|
| v0.1.0 | 2026-09-20 | 初版 | @ui-designer |
| v0.2.0 | 2026-09-20 | 改採 Stock-Insight-Chat 深色單色設計語言；**移除全部 emoji 圖示**，狀態改用圓點 + 文字；補齊尺寸/比例 token（控制項高度、圖示尺寸、版面寬度） | @ui-designer |
