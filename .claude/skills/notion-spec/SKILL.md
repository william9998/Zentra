---
name: notion-spec
description: 把 REST API、MCP、Gateway、Agent 執行流程、PostgreSQL／Redis 等設計內容同步到 Notion「系統設計」底下對應的 inline 資料庫；也可以檢查這些資料庫的分類是否重複並提出調整
argument-hint: [要記錄的設計主題；或「檢查分類」]
disable-model-invocation: true
---

把系統設計內容同步到 Notion「系統設計」。

使用者補充說明：$ARGUMENTS

## 先讀
- `.claude/notion-workspace.md`：位置、資料庫快取、共用規則（一定要照著做，特別是**不使用任何表情符號**）。
- 系統設計頁面：https://app.notion.com/p/41c2dbee508080eb85e4ca280f8e872e

## 來源對照

repo 的 `specs/` 有五份規格，Notion 的資料庫以這五塊為骨架（實際資料庫名稱以 fetch 結果為準，對應關係記進快取表）：

| 主題 | spec | 程式碼（以這裡為準） |
|---|---|---|
| REST API | `specs/api.md` | `apps/api/routers/agents.py`、`runs.py`、`mcp_keys.py`；`apps/api/schemas.py`；`apps/api/errors.py` |
| MCP 接口 `/mcp` | `specs/mcp.md` | `apps/api/mcp.py` |
| Gateway（`/internal/*`、LLM、出口、寄信） | `specs/gateway.md` | `apps/api/routers/internal.py`、`apps/api/services/` |
| Agent 撰寫、發布、執行流程 | `specs/agent.md` | `apps/worker/tasks/`、`apps/sandbox/`、`packages/sdk/`、`docker/` |
| PostgreSQL + Redis | `specs/db.md` | `apps/api/migrations/*.sql`、`apps/api/enums.py` |
| 架構與部署 | `CLAUDE.md`、`README.md` | `compose.yaml` |

## 範圍判斷
- 有補充說明 → 照說明。
- 沒有 → 用 `git log --oneline -15` 和 `git diff main...HEAD --stat` 看這個分支改了什麼，依上表找出受影響的設計。
- 內容以**程式碼**為準；和 `specs/*.md` 不一致的地方在回報裡列出（CLAUDE.md 要求改行為時同步改 spec，所以不一致通常代表 spec 漏改）。
- 一次改動常常要更新好幾個地方（例如新增 API＋新增欄位＋流程變了）。

## 一、REST API

- 一個資源種類一列（例：`00 通用約定`、`Agents`、`Runs`、`MCP Keys`、`Internal（Gateway）`）。若既有結構是「種類頁面裡再放 inline 資料庫『API 列表』」，就照那個結構，一支 API 一列，標題 `METHOD /路徑`。
- **`00 通用約定`**：認證方式、錯誤格式與 code（`apps/api/errors.py`、`apps/api/enums.py`）、分頁、冪等、SSE、端點總數。
- **每一支 API** 要有：
  1. `## 用途`：誰在什麼情況呼叫（Console、外部程式、容器裡的 Agent）
  2. `## Request`：Headers 表格、Path／Query 參數表格、Body（JSON 範例＋欄位表：欄位、型別、必填、限制、說明；沒有寫「無」）
  3. `## Response`：成功（狀態碼、Body 範例）、錯誤表格（狀態碼、code、何時）
  4. `## 注意事項`：冪等、Run 狀態機限制、非同步、副作用
- 欄位與限制以 `apps/api/schemas.py` 的 Pydantic 模型為準；回應以 router／service 實際回傳為準。
- 新增或刪除 API 時，同步更新種類頁面的端點總表與 `00 通用約定` 的端點總數。

## 二、MCP 與 Gateway
- MCP：tool 怎麼從已發布的 Agent 產生、認證（MCP key）、呼叫流程、錯誤回法。
- Gateway：容器怎麼透過 `app` 拿 LLM、出口白名單規則（含拒絕平台自己的主機）、寄信；強調**容器沒有長期憑證、唯一對端是 `app`**。
- 內文用 ```txt 畫請求路徑（容器 → app → 外部），附設計理由。

## 三、Agent 執行流程
- 發布：push 到內部 Registry → 凍結成 digest 版本 → 狀態變化。
- 執行：`q.run` 派工 → 容器（常駐／一次性）→ 事件回報 → Run 結束；`q.build` 做什麼。
- Run 狀態與錯誤 code 一律引用 `apps/api/enums.py` 的值，不要自己取名。

## 四、資料表（PostgreSQL）與 Redis
- **一張表一列**，標題就是表名；另有一列 `00 總覽`（全部表的 ER 圖、外鍵、索引）。
- **每張表的頁面**：`## 用途` → `## ER 圖`（```mermaid erDiagram，這張表＋直接相關的表）→ `## 欄位`（表格：欄位、型別、必填、預設、說明）→ `## 約束` → `## 外鍵`（含被誰參照）→ `## 索引`（每個說明用途）→ `## 重點`。
- **取得最終結構**：開暫時的 `pgvector/pgvector:pg16` 容器（和 `compose.yaml` 同版本），依序套用 `apps/api/migrations/*.sql`，從 `pg_attribute`、`pg_constraint`、`pg_index`、`pg_get_indexdef` 讀結構，用完刪除容器。不要只看單一 migration 檔（後面的 ALTER 會改掉它）。不要動 compose 裡正在跑的 postgres。
- Redis：一個用途一列（佇列、鎖、快取、SSE 等），寫 key 格式、TTL、誰寫誰讀，以 `specs/db.md` 和程式碼為準。
- 只更新這次動到的表；新表新增一列並更新 `00 總覽`。刪表時先問使用者要不要把那列移到垃圾桶。

## 五、設計問答
使用者在對話裡問「為什麼／要不要／怎麼選」並做出決定時，若有對應的資料庫（例如「問題」「決策」），記成一列，標題用問句，內文：問題背景、選項比較、結論、影響到的檔案。沒有對應資料庫就問使用者要放哪。

## 寫入規則
1. fetch 系統設計頁面與目標資料庫，對照 `.claude/notion-workspace.md` 的快取表。
2. 用 `notion-search` 找既有列：有就更新對應段落，沒有才新增。
3. 不給頁面 icon、不用 callout、不用任何表情符號。
4. 寫完抽查：fetch 一頁確認表格、toggle、mermaid 正常。
5. 回報：每個資料庫新增／更新了哪些列與連結；程式與 spec 不一致的地方另外列出。

## 「檢查分類」模式
使用者輸入「檢查分類」或覺得資料庫重複時：
1. fetch 所有資料庫的名稱、欄位、既有列標題。
2. 輸出一張表：每個資料庫放什麼、和哪個重疊、建議保留／合併／改名。
3. 提出建議的分類（以上面「來源對照」的五塊為參考），說明每個既有列要搬到哪。
4. **只提案，等使用者同意後才搬移或改名**；刪除資料庫或頁面一定要使用者明確同意。
5. 同意後更新 `.claude/notion-workspace.md` 的快取表。
