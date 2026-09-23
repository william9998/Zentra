# Zentra Agent Platform

> **每個 Claude Code session 開工前的必讀順序：**
> 1. 這份 `CLAUDE.md`（你在這裡）
> 2. [`specs/SPEC.md`](specs/SPEC.md) — 唯一事實來源
> 3. [`AGENTS.md`](AGENTS.md) — 團隊怎麼協作
> 4. [`coordination/BOARD.md`](coordination/BOARD.md) — 現在該做什麼

## 這是什麼

一個 **Agent 平台**：開發者上傳自己的 Python 程式碼（LangGraph），
平台凍結成不可變版本、在**隔離沙盒**中執行，自動對外開放 **REST API** 與 **MCP** 兩種接口。

核心技術有兩塊：**沙盒隔離執行** 與 **Celery 派工**。
Agent 框架統一用 **LangChain + LangGraph**，不接受其他框架。

> **已依 [ADR-0005](specs/adr/ADR-0005-mvp-single-host-scope.md) 降維（SPEC v0.2.0）。**
> 交付條件：**內部工具 · 單租戶 · 只跑信任的程式碼 · 八週 MVP**。
> 部署是**一台主機跑 `docker compose`**，5 個長駐服務（`postgres` · `redis` · `registry` · `api` · `worker`），
> **沒有 Kubernetes、沒有 gVisor**。`registry` 是 [ADR-0009](specs/adr/ADR-0009-internal-registry-image-upload.md) 新增的內部 OCI Registry，只掛 `zentra-net`。
> 前端是**原生 HTML + CSS + ES modules，零建置**（[ADR-0006](specs/adr/ADR-0006-vanilla-frontend.md)），
> 沒有 React / Tailwind / npm，Console 由 `api` 直接服務靜態檔。
> 範圍收斂成一條主線：**push image** → 註冊 digest → 發布 → 沙盒執行 → REST + MCP → Run 觀測。
> （平台**不接收 image 檔案上傳**；image 留在 Registry，平台只存 metadata 與 digest。）
> **五條立場 P1–P5 一條沒鬆**，降的是實作維度。
> 這張交付條件表變了，整份規格就要重讀。

## 五條不可妥協的立場（違反就是 bug）

| | 立場 |
|---|---|
| **P1** | 使用者程式碼**永遠不在平台行程內執行**。Celery worker 只派工與監看，`exec` 一律在沙盒。 |
| **P2** | Agent 框架**統一 LangChain + LangGraph**。所有 agent 最終都是一個 `CompiledStateGraph`。 |
| **P3** | 每個 Agent **天生就有 API + MCP 兩個門面**，由平台從同一份 `entrypoints` 自動生成。 |
| **P4** | **沙盒內拿不到任何長期憑證**。沙盒網段無對外路由，唯一對端是 **Internal Gateway**（`api` 的 `/internal/*`）。 |
| **P5** | **Agent 版本不可變**。`version_id` = bundle 的 SHA-256，發布後目錄設唯讀，要改就發新版。 |

## 目錄地圖

```
specs/               📌 規格（唯一事實來源，架構師擁有）
  SPEC.md              主規格：分層、選型、契約、狀態機、NFR
  glossary.md          統一名詞（全隊用同一組字）
  api-contract.md      前後端唯一交會點
  ui-contract.md       設計 token 與元件規格
  adr/                 技術決策紀錄（為什麼選 A 不選 B）
                       ADR-0005 是降維決策，**先讀它**才知道砍了什麼
  features/<id>/       單一功能的 spec + 驗收條件
docs/architecture/   📐 架構圖（Mermaid，detailed-architecture.md 是主檔）
coordination/        🔄 agent 之間的溝通
  BOARD.md             任務看板 + SPEC-QUESTION
  handoffs/<id>/       交接單
  DRIFT.md             規格漂移稽核
.claude/agents/      🤖 七個角色 agent 的定義
.claude/commands/    ⚡ 斜線指令
apps/                💾 實作（尚未建立）
  api/       FastAPI：REST · MCP · SSE · Console 靜態檔 · Internal Gateway
  worker/    Celery：q.run（派工）· q.build（發布）
  sandbox/   SandboxDriver + DockerSandboxDriver + Sandbox Manager
  console/   原生 HTML/CSS/JS（5 個 .html + static/），由 api 服務，無建置
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
# 完整環境（本機與正式是同一套）
docker network create --internal zentra-sandbox-net
docker compose up -d            # postgres + redis + registry + api + worker
                                # Console → http://localhost:8000/console/agents

# 後端（要熱重載時把 api/worker 拿出來跑）
uv sync && uv run uvicorn apps.api.main:app --reload   # 同時服務 Console 靜態檔
uv run celery -A apps.worker.celery_app worker -Q q.run,q.build -l info
uv run alembic upgrade head
uv run pytest

# 沙盒映像（共用一份，不是 per-agent build）
docker build -t zentra/agent-runtime:1.0 -f docker/agent-runtime.Dockerfile .

# Agent base image（使用者的 agent image 以此為基底，ADR-0009 / Q-010）
docker build -t zentra/agent-base:1.0 -f docker/agent-base.Dockerfile .

# 前端：沒有指令。沒有 dev server、沒有建置、沒有 node_modules。
# 改 apps/console/**.html|.css|.js → 重新整理瀏覽器。
```

## 給 Claude 的提醒

- **不要**在 `apps/worker/` 或 `apps/api/` 寫任何載入、執行使用者程式碼的邏輯（違反 P1）。
  這包含「只是 import 一下檢查 `build()` 在不在」—— 那也要在一次性沙盒裡做。
- **不要**把 API key / secret 寫進沙盒的 env 或 manifest（違反 P4）。
- **不要**讓沙盒直連 postgres 或 redis。沙盒的網路對端有且只有一個：`api`。
- Run 狀態字串與錯誤 code **一律引用枚舉常數**，照抄 `specs/SPEC.md` §9 與 `specs/api-contract.md`，不要自創。
  特別注意 `WAITING_INPUT` 在 v0.2.0 **已移除**，不要寫也不要斷言。
- **不要**把 `coordination/BOARD.md` §刻意不做 的東西「順手」加回來（知識庫、Tool Registry、
  平台組裝式、K8s…）。發現該做 → 記在 BOARD，不要自己加戲。
- **不要**在前端引入任何執行期函式庫或建置工具（React / Vue / Alpine / htmx / jQuery /
  bundler / TypeScript / npm 一律不准，違反 ADR-0006）。覺得需要 → 開 `SPEC-QUESTION`。
- **不要**用 `innerHTML` 寫入任何來自 API 的字串（vanilla 最容易出的 XSS），一律 `textContent`。
- 寫程式碼前先讀鄰近的檔案，命名、結構、錯誤處理跟著現有慣例走。
- 這個專案還沒有實作程式碼。要開始實作時，先看 `coordination/BOARD.md` 的 `ZEN-000`。
