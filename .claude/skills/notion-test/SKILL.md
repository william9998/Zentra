---
name: notion-test
description: 對本機 Zentra 做驗證（curl／Console 流程、效能壓測）或整理剛完成的除錯過程，並把結果記錄到 Notion「系統測試」底下對應 inline 資料庫的「測試項目」裡
argument-hint: [要測的東西（api／mcp／e2e／perf）或要記錄的問題]
disable-model-invocation: true
---

執行驗證或整理除錯過程，記錄到 Notion「系統測試」。

使用者補充說明：$ARGUMENTS

## 先讀
- `.claude/notion-workspace.md`：位置、資料庫快取、共用規則（一定要照著做，特別是**不使用任何表情符號**）。
- 系統測試頁面：https://app.notion.com/p/40a2dbee508080fa9a37cbad61a863c6

## Notion 結構（三層）

實際資料庫名稱以 fetch 結果為準，記進快取表。預期是：

```txt
系統測試（頁面）
 ├─ 某個 inline 資料庫（例：API、MCP、資安、效能、整合）   一個種類／情境一列
 │    └─ 每一列的頁面裡有 inline 資料庫「測試項目」
 │         └─ 每一列是一個測試案例或一次 bug 紀錄
```

- **第二層頁面**（例：`Runs`）：開頭寫測什麼、怎麼跑，最後是 inline 資料庫「測試項目」。
- **「測試項目」欄位**（既有資料庫的欄位優先，沒有時用這組）：
  - `Request`（標題）：`[通過] YYYY-MM-DD 測試名稱`；失敗用 `[失敗]`，修好前的 bug 用 `[未修正]`
  - `簡述`（文字）：一句話結果
  - `BUG程度`（select）：緊急／中等／輕微／純測試
  - `結果`（select）：通過／失敗／未執行
  - `測試檔案`（文字）：指令或腳本位置，例 `test/performance/locustfile.py`
- 第二層頁面還沒有「測試項目」時，用 `notion-create-database` 建在該頁面底下，欄位照上面。
- 系統測試頁面底下完全沒有資料庫時，先提出建議（API、MCP、資安、效能、整合），等使用者同意再建。

## 1. 執行驗證（使用者要求測時）

這個專案**沒有測試套件**（見 `CLAUDE.md`），驗證方式是：

| 說法 | 怎麼做 | 前置條件 |
|---|---|---|
| 語法 | `python3 -m py_compile` 改到的檔案 | 無 |
| api／rest | `docker compose up -d --build` 後用 curl 打 `specs/api.md` 的端點，照真實流程走（建 Agent → 發布 → 建 Run → 看事件／結果） | Docker；需要 `examples/` 的 Agent image 已 push 到 `localhost:6000` |
| mcp | 用 MCP key 對 `/mcp` 走 `tools/list` → `tools/call`，參照 `specs/mcp.md` | 至少一個已勾進 MCP 的 Agent |
| e2e／Console | 用瀏覽器在 Console 走一次發布到執行 | app 在跑 |
| perf／效能 | 照 `test/performance/README.md`（Locust） | **會寫進正在用的資料庫，先問使用者**；跑完照 README 收尾 |

- 會呼叫真的 LLM（花錢）的流程，先問使用者；效能測試用 `test/performance/mock_llm.py`。
- 只停自己啟動的東西，不要 `docker compose down -v`、不要刪資料。
- 記下：指令、環境、commit（`git rev-parse --short HEAD`，有未提交修改要註明）、結果、失敗時的錯誤摘要（`docker compose logs app`／`worker`）。
- 失敗時先排查原因；要改程式前先問使用者。

## 2. 寫入 Notion
1. fetch 系統測試頁面與底下的資料庫，依性質選資料庫與第二層頁面：
   - 測某組 REST API 的行為 → API（種類名稱與「系統設計」一致）
   - `/mcp` → MCP
   - 容器隔離、憑證外洩、出口白名單、MCP key 權限 → 資安（同一個測試可以同時記在 API 與資安）
   - 效能 → 效能
   - 跨服務的完整流程（push → 發布 → Run → 結果）、Console 操作 → 整合
   - 分不出來先問使用者
2. 第二層頁面不存在就新增一列（不給 icon），並在頁面裡建「測試項目」資料庫。
3. 每個測試案例一列；同一個案例已存在就更新結果與日期，不要重複新增。
4. 測試項目頁面內文：
   - **測試範圍**：測什麼、為什麼
   - **步驟／請求**：指令或 HTTP 請求（token、key 用 `<MCP_KEY>` 這類佔位，不寫實際值）
   - **預期**、**實際**
   - **環境**：本機 compose、commit、日期、有沒有用真的 LLM
   - 除錯紀錄時：**症狀** → **排查過程** → **根本原因** → **修正方式**（檔案、commit）→ **預防／後續**
   - 效能測試：RPS、p50／p95／p99、錯誤率表格，和上次結果比較
5. 對話裡沒有的細節不要猜，標「未確認」。不使用任何表情符號。
6. 回報：寫在哪個資料庫、哪一列、哪些測試項目、連結。
