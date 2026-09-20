# UI 契約 (UI Contract)

| 欄位 | 值 |
|---|---|
| 契約版本 | `v0.1.0` |
| 擁有者 | `@ui-designer` |
| 對齊 Spec | `specs/SPEC.md v0.1.0` |

> **`@frontend-dev` 只能引用這份文件的 token，不得寫死色碼、間距、字級。**
> 要新增 token 或元件 → 開 `SPEC-QUESTION`。

---

## 1. Run 狀態的視覺語言（與 SPEC §9 一一對應）

**七個狀態，不多不少。** 狀態不能只靠顏色區分，必須同時有圖示與文字。

| 狀態 | Token | 圖示 | 文案（繁中） | 語意 |
|---|---|---|---|---|
| `QUEUED` | `--color-status-queued` | ⏳ 時鐘 | 排隊中 | 中性灰，等待派工 |
| `SCHEDULING` | `--color-status-scheduling` | ⚙️ 齒輪 | 配置沙盒中 | 中性灰 + 脈動動畫 |
| `RUNNING` | `--color-status-running` | ▶ 播放（動態） | 執行中 | 藍，**需顯示當前節點與已耗時** |
| `WAITING_INPUT` | `--color-status-waiting` | ✋ 手 | 等待確認 | 琥珀，**需要 CTA 按鈕** |
| `SUCCEEDED` | `--color-status-succeeded` | ✓ 勾 | 已完成 | 綠 |
| `FAILED` | `--color-status-failed` | ✕ 叉 | 失敗 | 紅，**必須顯示 `error.code` 與重試按鈕（若 retryable）** |
| `CANCELLED` | `--color-status-cancelled` | ⊘ 禁止 | 已取消 | 中性灰（**與 FAILED 視覺上要能區分**） |

## 2. 設計 Token

所有 token 都有淺色 / 深色兩組值。元件只引用 token 名稱。

```css
:root {
  /* 語意色 — 不要用 --blue-500 這種實作色名 */
  --color-bg-canvas;      --color-bg-surface;     --color-bg-subtle;
  --color-border-default; --color-border-strong;  --color-border-focus;
  --color-text-primary;   --color-text-secondary; --color-text-disabled;
  --color-accent;         --color-accent-hover;
  --color-danger;         --color-warning;        --color-success;  --color-info;

  /* Run 狀態（§1） */
  --color-status-queued; --color-status-scheduling; --color-status-running;
  --color-status-waiting; --color-status-succeeded; --color-status-failed;
  --color-status-cancelled;

  /* 間距 4px 基準 */
  --space-1: 4px; --space-2: 8px;  --space-3: 12px; --space-4: 16px;
  --space-6: 24px; --space-8: 32px; --space-12: 48px;

  /* 字級 */
  --text-xs: 12px; --text-sm: 13px; --text-base: 14px;
  --text-lg: 16px; --text-xl: 20px; --text-2xl: 24px;
  --font-mono: ui-monospace, "SF Mono", monospace;   /* ID、payload、log 一律等寬 */

  --radius-sm: 4px; --radius-md: 6px; --radius-lg: 10px;
  --duration-fast: 120ms; --duration-base: 200ms;
}
```

**深色模式**：`[data-theme="dark"]` 重新定義同一組 token。元件不需要知道目前是哪個主題。

## 3. 元件五態規範

**每個元件規格都必須定義五種狀態，缺一不准交付。**

| 狀態 | 規則 |
|---|---|
| Loading | 已知版面用骨架屏；未知用 spinner。**超過 3 秒必須顯示進度語意**（在做什麼、第幾步） |
| Empty | 說明「這裡會出現什麼」+ 一個明確的 CTA。不要只顯示「無資料」 |
| Error | 必須含 `error.code`、`run_id`（若有）、「我現在該做什麼」。`retryable` 時提供重試 |
| Streaming / Partial | 資料還在來時的樣子。含中斷按鈕與部分內容的可複製性 |
| Success | 正常態 |

## 4. 核心元件規格

### `<RunStatusBadge status>`
圖示 + 文字 + 色彩，三者皆來自 §1 表格。`RUNNING` 與 `SCHEDULING` 帶脈動動畫。

### `<StreamingOutput runId>`
- 逐字渲染，**用 ref 累積 + 節流 flush（建議 60ms）**，不要每個 token 觸發 re-render
- `aria-live="polite"`（不用 `assertive`，會洗屏讀器）
- 右上角常駐「停止」按鈕 → 送 `POST /v1/runs/{id}/cancel`（不是只關本地連線）
- 連線中斷顯示「重新連線中…」，帶 `Last-Event-ID` 自動重連

### `<RunTimeline runId>` — 產品最重要的元件
- 事件依時間排列，節點可展開/收合，**預設收合**
- 工具呼叫展開後顯示 request / response，大 payload 折疊 + 提供「複製」
- 超過 200 筆事件啟用虛擬捲動
- **目標：工程師從「這次跑壞了」到「哪個節點的哪次工具呼叫回了什麼」不超過 30 秒**

### `<AgentWizard>`
步驟：來源 → Tools → Skills → 知識庫 → 模型/Prompt → 預覽發布
- 每步可回上一步且**保留已填內容**
- 未存草稿離開要攔截（`beforeunload`）
- 發布是不可逆操作 → 二次確認並說明影響範圍

### `<FlowCanvas>` (React Flow)
- 畫布狀態與節點資料分離；存檔只存 flow DAG
- 大圖啟用 `onlyRenderVisibleElements`
- 編輯中未存檔攔截離開

## 5. 無障礙底線（不可妥協）

- 所有互動元素可鍵盤操作，focus ring 清楚可見（`--color-border-focus`）
- 文字對比 ≥ 4.5:1，大字 ≥ 3:1
- 狀態不能只靠顏色（§1 每個狀態都配了圖示與文字）
- 串流區塊 `aria-live="polite"`
- 動畫尊重 `prefers-reduced-motion`

## 6. 變更紀錄

| 版本 | 日期 | 變更 | 由 |
|---|---|---|---|
| v0.1.0 | 2026-09-20 | 初版：Run 狀態視覺語言、token、五態規範、核心元件 | @ui-designer |
