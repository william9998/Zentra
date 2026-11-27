---
name: notion-test
description: 對本機 Zentra 實際跑測試（語法、REST／MCP、整合、資安隔離、負載），跑完後才把結果記錄到 Notion「系統測試」底下對應分類（inline 資料庫）的一列
argument-hint: [要測什麼：api／mcp／整合／資安／負載／語法，或改了哪個功能；不給就依這個分支的改動判斷]
disable-model-invocation: true
---

實際跑測試，跑完才把結果記錄到 Notion「系統測試」。

使用者補充說明：$ARGUMENTS

## 先讀
- `.claude/notion-workspace.md`：位置、分類模型、資料庫快取、共用規則（一定要照著做，特別是**不使用任何表情符號**）。
- 系統測試頁面：https://app.notion.com/p/40a2dbee508080fa9a37cbad61a863c6

## 鐵則：沒跑完就不寫 Notion

1. **寫入 Notion 之前，這個對話裡一定要已經實際執行過測試指令，並拿到輸出。** 只讀程式碼、只推論、引用以前的結果，都不算測試。
2. 環境起不來（Docker 沒開、服務不健康、映像 build 失敗）→ **不寫 Notion**，回報卡在哪一步、附輸出，問使用者要修環境還是改測別的。環境跑不起來不等於通過，也不寫成通過。
3. 測試有跑但失敗 → 照實記成 `[失敗]`。失敗也是測試結果，要記。先排查原因寫進紀錄；要改程式前先問使用者。
4. 一個案例只有部分步驟跑到 → 沒跑到的步驟在紀錄裡標「未執行」，整列不能標 `[通過]`。
5. 寫入的每個結論都要對得上一段實際輸出（狀態碼、回應欄位、log 行、數字）。

## 1. 決定測什麼

- 有補充說明 → 照說明。
- 沒有 → `git diff main...HEAD --stat` 加 `git status --short` 看改了什麼，依下表挑測試。都沒改就問使用者要測什麼。

| 改了什麼 | 至少要跑 | 分類 |
|---|---|---|
| 任何 `.py` | 語法：`python3 -m py_compile <改到的檔案>` | 雜項（只有這項時） |
| `apps/api/routers`、`schemas.py`、`services/` | 重建 `app` → curl 走改到的端點（成功與錯誤情境都要） | API |
| `apps/api/mcp.py`、MCP key | 用 MCP key 對 `/mcp` 走 `tools/list` → `tools/call` | API |
| `apps/worker`、`apps/sandbox`、`packages/sdk`、`docker/` | 重建 `worker` → 整合流程（push → 發布 → invoke → 結果）＋資安隔離清單 | 整合（UI)、資安 |
| Gateway（`/internal/*`、出口白名單、LLM 代理） | 從容器裡打 `/internal/*` 與外部位址 → 資安隔離清單 | 資安 |
| `apps/console` | 重建 `app` → 瀏覽器在 Console 走一次改到的畫面 | 整合（UI) |
| 效能、併發、資源設定 | 照 `test/performance/README.md` 跑 Locust | 負載 |

## 2. 怎麼跑（這個專案沒有自動化測試套件）

**起環境**（照 `README.md`）：

```bash
docker build -t zentra/agent-base:1.0 -f docker/agent-base.Dockerfile .
docker compose up -d --build
docker compose exec -T app python -m apps.api.migrations
curl -s localhost:8000/healthz
```

- 改了 `apps/api` 或 `apps/console` → `docker compose up -d --build app`；改了 `apps/worker` 或 `apps/sandbox` → `docker compose up -d --build worker`。不重建測到的是舊程式碼。
- 測試用 Agent 用 `examples/probe-agent`（`docker build -t localhost:6000/probe-agent:<版本> examples/probe-agent && docker push ...`），等 Console 顯示 `PUBLISHED` 再呼叫。
- 端點、欄位、錯誤 code 以 `specs/api.md`、`specs/mcp.md`、`specs/gateway.md` 為準；Run 狀態與錯誤 code 用 `apps/api/enums.py` 的值。

**資安隔離清單**（動到容器、憑證、網路出口、Run 執行路徑時才跑，在容器內執行）：
- 從容器**直接**連 `postgres:5432`、`redis:6379`、`registry`、外網（例 `1.1.1.1`）→ 失敗；連 `app:8000/internal/...` → 成功（`specs/agent.md`〈容器限制〉：沒有對外路由，只連得到 `app`）。
- 寫 `/`、`/app` → 失敗；寫 `/workspace` → 成功；`id` → `65534`（非 root）。
- `env` 裡沒有 LLM key、DB 密碼、使用者機密。
- 打別人的 Run 的 `/internal/*` → `403`；無效或已撤銷的 token → `401`（`specs/gateway.md`）。
- 經 Gateway 代打（`ctx.http`）：不在白名單 → `EGRESS_BLOCKED`；白名單寫平台服務名或 loopback → 被擋；3xx → `EGRESS_BLOCKED`（`REDIRECT`）。
  - 已知限制：spec 寫明代打**不檢查內網**，平台內網 IP、`host.docker.internal` 仍連得到。測到這點記成「已知限制（spec 明定）」，不記成失敗，也不寫成已防護。
- 驗不到的那層標「未執行」，不准標通過。結論寫「Docker 層隔離」，不要寫「沙盒安全」。

**負載**：照 `test/performance/README.md`。它會寫進正在使用的資料庫，**跑之前先問使用者**；用 `mock_llm.py`，不打真的 LLM。

**共通**：
- 會呼叫真的 LLM（花錢）的流程，先問使用者。
- 只停自己啟動的東西。不要 `docker compose down -v`、不要刪資料。
- 邊跑邊記：指令、輸出重點、環境、commit（`git rev-parse --short HEAD`，有未提交修改要註明）、失敗時的 `docker compose logs app`／`worker` 摘要。
- 測完把自己建的測試 Agent、Run、MCP key 清掉或在回報裡列出來。

## 3. 寫入 Notion（第 2 步確實跑完才做）

1. fetch 系統測試頁面，確認分類（inline 資料庫）有沒有變，對照快取表。
2. 依第 1 步的「分類」欄選資料庫。分不出來先問使用者；沒有合適分類時照 `.claude/notion-workspace.md`〈分類模型〉第 3、4 點提案、等同意再建。
3. **一個測試案例一列**，用案例名稱找既有列（`notion-search` 或 rows 查詢）：
   - 已存在 → 改列標題的狀態前綴為這次結果，在內文「執行紀錄」表格**最上面**加一行，其他段落有變才改。
   - 不存在 → 新增一列。
4. **列標題**：`[通過] 測試案例名稱`、`[失敗] …`、`[部分通過] …`。案例名稱寫測什麼，例：`Runs：invoke 同步呼叫`、`容器無法連 postgres`、`InvokeUser 20 人 2 分鐘`。標題避開會被變成連結的字（`q.run`、`agent.py`）。
5. **列內文**（不用 icon、callout、表情符號）：

   ```txt
   ## 摘要            表格：結果、日期、commit、分支、環境（本機 compose）、是否用真的 LLM、BUG 程度（緊急／中等／輕微／純測試）
   ## 測試範圍        測什麼、為什麼要測（對應哪個改動）
   ## 步驟            指令或 HTTP 請求（```bash）；token、key 用 <MCP_KEY>、<RUN_TOKEN> 佔位，不寫實際值
   ## 預期與實際      表格：步驟、預期、實際、結果
   ## 輸出節錄        關鍵輸出，放在 h3 toggle 裡（長 log 只留相關幾行）
   ## 執行紀錄        表格：日期、commit、結果、備註；新的放最上面
   ```

   - 失敗時加 `## 排查`：症狀 → 排查過程 → 根本原因 → 修正方式（檔案、commit；還沒修寫「未修正」）→ 後續。
   - 負載測試加 `## 數據`：RPS、p50／p95／p99、錯誤率，和上一次紀錄比較。
6. 同一次測試涵蓋好幾個案例 → 一個案例一列，不要全部塞同一列。
7. 寫完 fetch 一列抽查表格、toggle 是否正常。

## 4. 回報
- 跑了哪些指令、通過／失敗幾項（附關鍵輸出）。
- 寫在哪個分類、哪些列（新增或更新）、Notion 連結。
- 沒測到的部分、標「未執行」的步驟。
