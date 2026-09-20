# ADR-0006：Console 改用原生 HTML + CSS + JS，取消 console 服務

| | |
|---|---|
| 狀態 | **已採納 (Accepted)** |
| 日期 | 2026-09-20 |
| 決策者 | @architect（依專案負責人裁示） |
| 相關 | 延續 [ADR-0005](ADR-0005-mvp-single-host-scope.md) 的降維方向；影響立場 P3 |

## 背景

ADR-0005 已經把基礎設施降到 5 個服務、範圍收成一條主線。剩下的重量集中在前端：

原選型是 **Next.js 15 + React 19 + TypeScript + Tailwind + shadcn/ui + TanStack Query + Zustand + pnpm**。
對一個**五個畫面的內部工具**來說，這套東西的成本不在寫畫面，而在它自己：
Node 工具鏈、建置步驟、型別產生、套件升級、SSR/CSR 的邊界問題、第六個 compose 服務。

同時有一個現成的條件沒有被利用：**這些畫面要顯示的資料，全部來自一份已經定好的 JSON 契約**
（`specs/api-contract.md`），而且最重要的畫面（Run 觀測）靠的是 **SSE** ——
那是瀏覽器原生的 `EventSource`，用框架反而要跟它的生命週期打架。

## 決定

### 一、前端技術：原生三件套，零建置

| | |
|---|---|
| HTML | 一個畫面一個 `.html`，共用外框由 `shell.js` 注入 |
| CSS | 原生 custom properties。**`ui-contract.md` 的 token 原封不動可用**——它本來就是純 CSS 變數 |
| JS | 原生 ES modules（`<script type="module">`），瀏覽器直接吃 |

**沒有** bundler、沒有 TypeScript 編譯、沒有 npm / pnpm、沒有 node_modules。
改一行 CSS 就重新整理看到結果。

不引入任何執行期函式庫，包含 React / Vue / Alpine / htmx / jQuery。
需要的三樣東西都有原生對應：

| 需求 | 原生做法 |
|---|---|
| 串流 | `EventSource`（SSE 是瀏覽器原生能力） |
| 資料取用 | `fetch` + 一個 `api.js` 薄包裝（統一錯誤格式、統一 base URL） |
| 狀態共享 | 一個 ~40 行的 `store.js`（發布／訂閱），只有 Run 觀測頁需要 |

### 二、取消 `console` 服務，由 `api` 直接服務靜態檔

**compose 從 5 個服務降到 4 個**：

```
postgres · redis · api · worker
```

`api` 用 FastAPI 的 `StaticFiles` 掛載 `apps/console/`，並用五條 `FileResponse` 路由給出乾淨網址：

| 網址 | 檔案 |
|---|---|
| `/console/agents` | `agents.html` |
| `/console/agents/{agent_id}` | `agent.html` |
| `/console/agents/{agent_id}/versions/new` | `version-new.html` |
| `/console/runs` | `runs.html` |
| `/console/runs/{run_id}` | `run.html` |

`/` 轉址到 `/console/agents`。靜態資產掛在 `/console/static/*`。

**沒有前端路由器。** 深層連結、上一頁、404 全部由伺服器處理，不需要寫也不會有邊界情況。
每個頁面從 `location.pathname` 讀出自己的 id。

### 三、Console 直接消費 `/v1`，不做 BFF

瀏覽器與 API **同源**，所以：

- 不需要 CORS 設定
- 不需要 Console BFF 這一層（原架構的 L0 有這個元件，**現在移除**）
- Console 用的就是外部系統用的那份 API

**最後一點是重點**：Console 變成 `api-contract.md` 的第一個真實消費者。
契約寫錯、欄位對不上，做畫面的時候就會發現，而不是等外部系統來踩。
**這強化了 P3，不是削弱它。**

### 四、認證（MVP 無，之後走 session cookie）

MVP 不做認證（與 [ZEN-000](../features/ZEN-000/spec.md) 一致）。之後補的時候：

- ✅ 同源 **session cookie**（`HttpOnly` + `SameSite=Lax`）
- ❌ **不要**把 `api_key` 放 `localStorage` —— 一個 XSS 就整把拿走，而且它是長期憑證

## 考慮過但否決的選項

- **維持 Next.js + React**
  否決理由：五個畫面撐不起這套工具鏈的成本。最實際的代價是**多一個 compose 服務**
  與**一個建置步驟**，而 ADR-0005 的整個方向就是在拆掉這兩種東西。

- **htmx + FastAPI 回傳 HTML 片段**
  否決理由：技術上很適合這個規模，但它會讓 `api` 同時回 JSON（給外部系統）與 HTML 片段（給 Console），
  等於維護兩套輸出。更糟的是 **Console 不再消費自己的 JSON 契約**，就失去了上面說的契約驗證效果。

- **Preact / Alpine 走 CDN（無建置但有框架）**
  否決理由：省下的東西（樣板語法、響應式）在五個畫面的規模上不值得多一個相依。
  真的寫到覺得痛，再回來看這條——那時候候選是 **Preact + htm**，不是 React。

- **單頁 SPA + hash 路由**
  否決理由：要自己寫路由器，並且要自己處理上一頁、深層連結、404。
  多頁 + 伺服器路由把這些全部變成別人的問題。

## 後果

### 正面
- compose **5 → 4 個服務**；repo 少掉整個 Node 工具鏈
- 沒有建置步驟：改檔 → 重新整理
- SSE 用原生 `EventSource`，比在 React 裡管生命週期單純
- Console 成為 API 契約的第一個消費者，契約錯誤提早暴露

### 負面（誠實列出）
- **沒有型別檢查。** 前後端欄位對不上只能在執行期發現。
  對策：`api.js` 集中處理回應，欄位名只在那裡出現一次；契約測試（G2）照跑。
- **沒有元件模型。** 共用 UI 靠 ES module 匯出的 render 函式 + `<template>`，紀律要自己顧。
  `ui-contract.md` 的元件規格因此更重要——它現在是唯一的約束。
- **手寫 DOM 更新容易出錯**，尤其 Run 觀測頁的事件流。
  對策：事件流採**只追加**（append-only）渲染，不做 diff；狀態變更整塊重繪。
- **虛擬捲動降級。** 原本要求「超過 200 筆啟用虛擬捲動」，vanilla 手刻成本太高，
  改為**只渲染最近 N 筆 + 往上載入更多**（見 `ui-contract.md` §5.3）。
- **Vitest 移除**，前端沒有單元測試層。Playwright 保留——它測瀏覽器，跟框架無關。

### 何時該重新檢討
- 畫面超過 **10 個**，或出現需要跨頁共享的複雜狀態
- 手寫 DOM 更新開始產生重現性 bug（不是偶發，是重現得出來的那種）
- 要做平台組裝式 Agent（那個畫面有大量動態表單，vanilla 會很痛）
