# Notion 工作區對照表（Zentra）

所有 `/notion-*` skill 共用這份檔案：寫入前先讀它，照這裡的位置和規則寫。

## 位置

根頁面「Zentra」：https://app.notion.com/p/3c92dbee50808056961ae43e798405d5

| 區塊 | 頁面 | 對應 skill |
|---|---|---|
| 產品規劃 | https://app.notion.com/p/41c2dbee508080be8cacca800612467c | `/notion-product` |
| 系統設計 | https://app.notion.com/p/41c2dbee508080eb85e4ca280f8e872e | `/notion-spec` |
| 系統開發 | https://app.notion.com/p/3e72dbee50808020b710e6a4331d3163 | `/notion-dev` |
| 系統測試 | https://app.notion.com/p/40a2dbee508080fa9a37cbad61a863c6 | `/notion-test` |

根頁面只放專案簡介和這四個區塊的連結；要更新根頁面（例如專案簡介過時）時，內容取自 `README.md` 與 `CLAUDE.md` 開頭，只改簡介段落，不動子頁面。

## 資料庫快取

第一次執行某個 skill 時，fetch 該區塊頁面，把找到的 inline 資料庫填進下表。之後先用這張表，**寫入前仍要 fetch 目標資料庫確認欄位沒變**；有變就更新這張表（這份檔案會進 git，更新後提醒使用者提交）。

| 區塊 | 資料庫名稱 | data source（collection://…） | 標題欄位 | 其他欄位 | 放什麼 |
|---|---|---|---|---|---|
| 系統設計 | API | `collection://2cb2dbee-5080-830c-b74b-077dab781aca` | `Name` | — | 一個 API 種類一列（`00 通用約定`、`Agents：…`、`Runs：…`、`常駐容器`、`下游 API key 與用量`、`Agent 設定與對外能力`、`MCP Keys`、`Gateway：/internal/*…`）；每支端點是種類頁面裡的 h3 toggle（用途／Request／Response／注意事項） |
| 系統設計 | 背景任務＆Redis | `collection://b5d2dbee-5080-83bf-a331-0740b246ba16` | `Name` | — | Agent 發布流程、Agent 執行流程、常駐容器生命週期與排程、Celery 佇列與任務、Redis 用法總覽（一個主題一列） |
| 系統設計 | PostgreSQL設計 | `collection://b9f2dbee-5080-83ac-a0ac-070a9c8c89f4` | `Name` | — | 一張表一列（標題就是表名），另有 `00 總覽`（ER 圖、全部外鍵、全部索引） |
| 系統設計 | 部署與架構 | `collection://59d2dbee-5080-8244-9118-8755e00bf47b` | `Name` | — | 架構與部署主題一列；目前有「Agent 映像規格與容器隔離」 |
| 系統設計 | 問題 | `collection://55d2dbee-5080-826e-9846-87f5f0809953` | `Name` | — | 設計問答與決策紀錄，標題用問句（目前空） |

## 共用規則

1. **先讀後寫**：fetch 區塊頁面 → 決定資料庫 → fetch 資料庫看欄位與既有列 → 查有沒有同主題的列。
   - 有 → 更新該列的屬性和內文對應段落，不要整頁重寫、不要重複新增。
   - 沒有 → 新增一列。
2. **照既有欄位填**：不自己加欄位、不改欄位名稱或選項；select 沒有合適選項時才新增選項，並在回報裡說明。
3. **只寫在上表的四個區塊裡**：不新建頂層頁面；區塊底下還沒有任何資料庫時，先提出建議的資料庫清單與欄位，**等使用者同意後**才建立。
4. **判斷不了放哪個資料庫時，先問使用者**，列出候選資料庫和理由。
5. **內容以 repo 為準**：API、資料表、流程、指令從程式碼、`specs/*.md`、`README.md`、`compose.yaml` 讀，不要自己編。對話與 repo 裡都找不到的細節標「未確認」。
6. **使用者的想法 vs. Claude 的建議分開寫**：Claude 自己加的建議放在標題為「Claude 建議」的段落。
7. **內文風格**：`##` 分段、比較用表格、流程用 ```txt 區塊、ER 圖用 ```mermaid、長程式碼放 toggle；繁體中文、白話，術語第一次出現時補一句解釋。
8. **不使用任何表情符號（emoji）**：頁面 icon、標題、屬性值、內文、表格、callout 都不用。
   - 建立頁面時不給 `icon`；callout 一律改用 `<quote>` 或粗體引導詞（callout 會帶圖示）。
   - 狀態用文字：「通過」「失敗」「未修正」「已實作」「規劃中」。
   - 更新既有頁面時，順手把該頁的表情符號（含頁面 icon）移除。
9. **表格儲存格裡不要放 `|`**（會把欄位切斷）；寫完 fetch 一頁抽查表格、toggle、mermaid 是否正常。
   - 看起來像網域的字（`q.run`、`q.build`、`agent.py`）沒包在 code 裡會被 Notion 自動變成 `http://` 連結，**頁面標題也會**。內文一律用 code 包起來，標題避開這類字。
   - 改既有內容時 Notion 存的是 `<code>…</code>` 與 `| --- |` 格式，`update_content` 的 `old_str` 要照 fetch 回來的寫法。
10. **不寫機密**：`.env`、金鑰、token、MCP key、LLM 憑證的實際值一律不寫進 Notion，只寫變數名稱。
11. **完成後回報**：每個寫入位置（區塊 / 資料庫 / 列標題）、新增或更新、Notion 連結。

## Notion 方案限制

若 `notion-query-data-sources`（SQL／rows 查詢）回 `entitlement_required`，表示不是 Business 方案，不要再用。
查既有列改用：`notion-search`（關鍵字＋看結果的 `parent_data_source_url`），或 fetch 資料庫的 view URL；新增前用列標題搜尋一次避免重複。

## 連不上 Notion 時

`.mcp.json` 已設定 Notion MCP（`https://mcp.notion.com/mcp`），`.claude/settings.json` 已啟用。工具不存在或回 401 時，請使用者執行 `/mcp` → 選 notion → 登入授權，授權完再重跑 skill。不要改寫成本機檔案就當作完成。
