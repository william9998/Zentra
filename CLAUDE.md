# Zentra Agent Platform

> **每個 Claude Code session 開工前的必讀順序：**
> 1. 這份 `CLAUDE.md`（你在這裡）
> 2. [`specs/SPEC.md`](specs/SPEC.md) — 唯一事實來源
> 3. [`AGENTS.md`](AGENTS.md) — 團隊怎麼協作
> 4. [`coordination/BOARD.md`](coordination/BOARD.md) — 現在該做什麼

## 這是什麼

一個 **Agent 平台**：使用者用「平台組裝」或「上傳 Python 程式碼」建立 AI Agent，
每個 Agent 在**隔離沙盒**中執行，自動對外開放 **REST API** 與 **MCP** 兩種接口。

核心技術有兩塊：**沙盒隔離執行** 與 **Celery 派工**。
Agent 框架統一用 **LangChain + LangGraph**，不接受其他框架。

## 五條不可妥協的立場（違反就是 bug）

| | 立場 |
|---|---|
| **P1** | 使用者程式碼**永遠不在平台行程內執行**。Celery worker 只派工與監看，`exec` 一律在沙盒。 |
| **P2** | Agent 框架**統一 LangChain + LangGraph**。所有 agent 最終都是一個 `CompiledStateGraph`。 |
| **P3** | 每個 Agent **天生就有 API + MCP 兩個門面**，由平台從同一份 `entrypoints` 自動生成。 |
| **P4** | **沙盒內拿不到任何長期憑證**。對外一律走 Tool / LLM / Egress Proxy。 |
| **P5** | **Agent 版本不可變**。發布後 manifest + bundle + lockfile 凍結，要改就發新版。 |

## 目錄地圖

```
specs/               📌 規格（唯一事實來源，架構師擁有）
  SPEC.md              主規格：分層、選型、契約、狀態機、NFR
  glossary.md          統一名詞（全隊用同一組字）
  api-contract.md      前後端唯一交會點
  ui-contract.md       設計 token 與元件規格
  adr/                 技術決策紀錄（為什麼選 A 不選 B）
  features/<id>/       單一功能的 spec + 驗收條件
docs/architecture/   📐 架構圖（Mermaid，detailed-architecture.md 是主檔）
coordination/        🔄 agent 之間的溝通
  BOARD.md             任務看板 + SPEC-QUESTION
  handoffs/<id>/       交接單
  DRIFT.md             規格漂移稽核
.claude/agents/      🤖 七個角色 agent 的定義
.claude/commands/    ⚡ 斜線指令
apps/                💾 實作（尚未建立）
  api/ worker/ console/ sandbox/ proxy/
packages/sdk/        使用者在沙盒內寫 agent 時用的 zentra_sdk
```

## 開發鐵則

1. **SPEC 優先。** 程式碼與 SPEC 衝突 → 先改 SPEC 或先問，不准「順手」改變行為。
2. **不明就問。** 規格沒寫清楚 → 在 `coordination/BOARD.md` 開 `SPEC-QUESTION`，把任務標 `BLOCKED`，先做不相依的部分。**不要猜。**
3. **不越界。** 每個 agent 只寫自己的區域（見 `AGENTS.md` §1）。要別人改東西 → 開交接單。
4. **契約優先。** 前端不准猜欄位名，後端不准自創回應格式。兩邊都對 `specs/api-contract.md`。
5. **誠實回報。** 測試沒過就說沒過，附輸出。沒做的明講。不要用「已完成」帶過。
6. **不擴張範圍。** 發現順帶該修的 → 記在 BOARD，不要自己加戲。
7. **留下痕跡。** 每次交棒都要有交接單。

## 常用指令（尚未建立實作前為預定）

```bash
# 後端
uv sync && uv run uvicorn apps.api.main:app --reload
uv run celery -A apps.worker.celery_app worker -Q q.agent.short -l info
uv run alembic upgrade head
uv run pytest

# 前端
pnpm --filter console dev
pnpm --filter console test
pnpm --filter console build     # type error 也算失敗

# 本機完整環境
docker compose up -d            # postgres + redis + rabbitmq + minio + opensearch
```

## 給 Claude 的提醒

- **不要**在 `apps/worker/` 或 `apps/api/` 寫任何載入、執行使用者程式碼的邏輯（違反 P1）。
- **不要**把 API key / secret 寫進沙盒的 env 或 manifest（違反 P4）。
- Run 狀態字串與錯誤 code **一律引用枚舉常數**，照抄 `specs/SPEC.md` §9 與 §8.4，不要自創。
- 寫程式碼前先讀鄰近的檔案，命名、結構、錯誤處理跟著現有慣例走。
- 這個專案還沒有實作程式碼。要開始實作時，先看 `coordination/BOARD.md` 的 `ZEN-001`。
