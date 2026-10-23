# Zentra Agent Platform

一個 **Agent 平台**：開發者把自己的 Python agent（LangGraph）打包成 OCI image 推上 Registry，
平台凍結成不可變版本、在**隔離容器**中執行，自動對外開放 **REST API**，並可把 Agent 勾選進平台唯一的 **MCP 接口**（Agent = tools）。

交付條件：**內部工具 · 單租戶 · 只跑信任的程式碼**。一台主機 `docker compose`，5 個長駐服務
（`postgres` · `redis` · `registry` · `app` · `worker`），**沒有 Kubernetes、沒有 gVisor**。
前端是**原生 HTML + CSS + ES modules，零建置**。這張交付條件變了，整份規格要重讀。

## 五條不可妥協的立場（違反就是 bug）

| | 立場 |
|---|---|
| **P1** | 使用者程式碼**永遠不在平台行程內執行**。Celery worker 只派工與監看，`exec` 一律在容器裡。 |
| **P2** | Agent 框架**統一 LangChain + LangGraph**。所有 agent 最終都是一個 `CompiledStateGraph`。 |
| **P3** | 每個 Agent **天生有 REST 門面，並可被勾選進平台唯一的 MCP 接口（`/mcp`，Agent 是 tools）**，都由平台從同一份 `entrypoints` 自動生成。 |
| **P4** | **沙盒內拿不到任何長期憑證**。沙盒網段無對外路由，唯一對端是 **Internal Gateway**（`app` 的 `/internal/*`）。 |
| **P5** | **版本不可變**。以完整 OCI digest 識別，不接受 tag-only。一筆版本紀錄的 digest 永不改寫；同版號重新註冊 = 新增一筆並取代舊的。 |

## 規格：`specs/` 底下 12 份，每份都是現行事實

要動到什麼，只讀那一份（先 `grep -n '^#' <file>` 看目錄，再取那一節）。沒有 ADR、沒有歷史版本 —— 歷史在 `git log`。

| 要動的 | 讀 |
|---|---|
| 整體架構、P1–P5、名詞、程式碼註解裡舊編號的對照 | [`specs/overview.md`](specs/overview.md) |
| 對外 REST 端點、欄位、錯誤碼 | [`specs/api.md`](specs/api.md) |
| Run 派工、狀態機、終態、事件 | [`specs/dispatch.md`](specs/dispatch.md) |
| **寫 Agent**：怎麼寫、規則、`ctx` 能做什麼、常見錯誤 | [`specs/agent-development.md`](specs/agent-development.md) |
| Agent / 版本註冊、驗證、取代、刪除、下游 API key | [`specs/agent-registry.md`](specs/agent-registry.md) |
| 主機資源、上限、溢出、回收、逾時 | [`specs/dispatcher-resources.md`](specs/dispatcher-resources.md) |
| 容器建立、隔離、常駐容器生命週期 | [`specs/container-orchestration.md`](specs/container-orchestration.md) |
| Internal Gateway：token、LLM、出口、寄信、稽核 | [`specs/gateway.md`](specs/gateway.md) |
| MCP 門面 | [`specs/mcp.md`](specs/mcp.md) |
| 資料表 | [`specs/postgresql.md`](specs/postgresql.md) |
| Redis key | [`specs/redis.md`](specs/redis.md) |
| Console | [`specs/console-ui.md`](specs/console-ui.md) |
| 現在該做什麼、什麼是刻意不做的 | [`coordination/BOARD.md`](coordination/BOARD.md) |

**改行為 = 先改那一份 spec，再改程式碼，同一個 commit。** spec 與程式碼不一致就是 bug。

## 目錄地圖

```
specs/               📌 規格（12 份，見上表）
apps/
  api/       FastAPI：REST · MCP · SSE · Console 靜態檔 · Internal Gateway；migrations/ 是 schema 唯一來源
  worker/    Celery：q.run（派工）· q.build（發布、容器、回收）
  sandbox/   SandboxDriver + DockerSandboxDriver + SandboxManager
  console/   原生 HTML/CSS/JS，由 api 服務，無建置
packages/sdk/        使用者在容器內寫 agent 時用的 zentra_sdk
examples/            範例 agent（my-agent 是起點）
monitoring/          維運監控（Prometheus + Grafana + exporter），獨立部署，不屬於五個服務
scripts/             verify-zen000.sh（冒煙）· clean-test-data.sh · adopt-legacy-schema.sh
coordination/BOARD.md 任務看板
```

## 開發鐵則

1. **規格優先。** 程式碼與 spec 衝突 → 先改 spec 或先問，不准「順手」改變行為。
2. **不明就問 —— 直接問使用者。** 不要猜，也不要寫 `// TODO: 待確認`。先做不相依的部分。
3. **契約優先。** 前端不准猜欄位名，後端不准自創回應格式。兩邊都對 `specs/api.md`。
4. **誠實回報。** 驗證沒過就說沒過，附輸出。沒做的明講。不要用「已完成」帶過。
5. **不擴張範圍。** 發現順帶該修的 → 記在 BOARD 或回報裡，不要自己加戲。

## 驗證

repo 裡**沒有自動化測試套件**。驗證靠實際跑起來看：

| 改了什麼 | 怎麼驗 |
|---|---|
| 文件、規格、註解 | 不用驗 |
| 一般程式碼 | `python3 -m py_compile` / `pyflakes` 過；`make rebuild-app` 或重建 worker 後，用 curl 或 Console 把改到的那條路徑實際走一次 |
| 沙盒 / 憑證 / 網路出口 / Run 執行路徑 | 上面 + `make smoke` + [`/verify`](.claude/commands/verify.md) 的隔離清單 |

- 程式要在映像裡跑才算數：改了 `apps/` 就要重建 `app` / `worker`，不然測到的是舊程式碼。
- 呼叫的 `manager.X` / 屬性名要確認真的存在 —— 曾經有改名改到不存在的方法，例外被吞掉，Run 照樣成功，完全看不出來。
- **這次有在平台上建 Agent / Run** → 收工前 `make clean-test-data`。沒建就不用。內建 fixture `builtin-echo` 會自動留著。

## 什麼時候叫 subagent

預設**自己做**。只有跨後端 + Console + 沙盒的大改動，才派一個 `builder` 一次做完。不寫交接單。

## 常用指令

```bash
make up                         # 起服務 + build base image + migration + 健康檢查
make help                       # 所有指令
make smoke                      # 冒煙：verify-zen000.sh
make migrate                    # 純 SQL migration（沒有 Alembic）
make rebuild-app                # 改了 apps/console 或 apps/api 之後
docker compose up -d --build worker   # 改了 apps/worker 或 apps/sandbox 之後
make clean-test-data            # 有在平台上建測試 Agent / Run 才需要

# Console → http://localhost:8000/console/agents
# Agent base image（使用者的 agent image 以此為基底）
docker build -t zentra/agent-base:1.0 -f docker/agent-base.Dockerfile .
# 內建 fixture builtin-echo 的映像（只有冒煙測試用，tag 必須是 :dev）
docker build -t zentra/agent-runtime:dev -f docker/agent-runtime.Dockerfile .
```

主機上可能沒有 uv；在容器裡跑 Python：`docker compose exec -T app python -c '…'`。

## 給 Claude 的提醒

- **不要**在 `apps/worker/` 或 `apps/api/` 寫任何載入、執行使用者程式碼的邏輯（違反 P1）。
  這包含「只是 import 一下檢查 `build()` 在不在」—— 那也要在一次性容器裡做。
- **不要**把 API key / secret 寫進容器的 env 或 manifest（違反 P4）。env 是精確集合，不准放寬。
- **不要**讓沙盒直連 postgres、redis 或 registry。沙盒的網路對端有且只有一個：`app`。
- Run 狀態字串與錯誤 code **一律引用 `apps/api/enums.py` 的枚舉**，不要自創。`WAITING_INPUT` **已移除**。
- **不要**把 `coordination/BOARD.md` §刻意不做 的東西「順手」加回來。發現該做 → 記在 BOARD。
- **不要**在前端引入任何執行期函式庫或建置工具。覺得需要 → 先問使用者。
- **不要**用 `innerHTML` 寫入任何來自 API 的字串，一律 `textContent`。
- 寫程式碼前先讀鄰近的檔案，命名、結構、錯誤處理跟著現有慣例走。
