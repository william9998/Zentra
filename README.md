<div align="center">

# Zentra

**An agent platform: push your agent as a container image, get an isolated runtime, a REST API, and a slot on one shared MCP endpoint.**

[繁體中文](#繁體中文) · [Specs](specs/overview.md) · [Board](coordination/BOARD.md)

</div>

---

You write a Python agent with LangGraph, package it as an image and `docker push` it to the platform. The platform
freezes it as an immutable version, runs it in an isolated container, and exposes it as a **REST API**. Agents can
also be ticked into the platform's **single MCP endpoint (`/mcp`)**, where each one becomes a tool that AI apps such
as Claude or ChatGPT can call.

**Delivery conditions:** internal tool · single tenant · trusted code only · one host running `docker compose`
with five services (`postgres` · `redis` · `registry` · `app` · `worker`) · no Kubernetes · the console is plain
HTML/CSS/JS with no build step. Status: the main path works end to end; what is verified and what is still open is
tracked in [`coordination/BOARD.md`](coordination/BOARD.md).

## Five rules (breaking one is a bug)

| | Rule |
|---|---|
| **P1** | User code **never runs inside a platform process**. Workers only dispatch; code runs in containers. |
| **P2** | Every agent is **LangChain + LangGraph**, ending in one `StateGraph`. |
| **P3** | Every agent gets a **REST API** and can join the **single MCP endpoint**, both generated from the same `entrypoints`. |
| **P4** | **No long-lived credential enters a container.** Containers can only reach `app`, which holds the keys. |
| **P5** | **Versions are immutable**, identified by the full image digest. |

## Run it

```bash
make up                                   # 5 services + base image + migrations + health check
make help                                 # every command
open http://localhost:8000/console/agents # Console
```

- After changing `apps/api` or `apps/console`: `make rebuild-app`. After `apps/worker` or `apps/sandbox`: `docker compose up -d --build worker`.
- The registry is on host port **6000** (macOS AirPlay takes 5000).
- Start over with an empty database (**deletes all agents, runs and registered images**):
  `docker compose down -v && make up`.

## Settings (environment variables)

Optional. `cp .env.example .env`, edit, then `docker compose up -d` to apply. Every value below has a working default.

| Variable | Default | What it controls |
|---|---|---|
| `ZENTRA_LLM_API_KEY` | (empty) | Platform-wide LLM key for agents without their own. Empty → LLM calls fail with `LLM_ERROR` |
| `ZENTRA_LLM_BASE_URL` | OpenAI | Which LLM provider to call (any OpenAI-compatible endpoint) |
| `ZENTRA_LLM_DEFAULT_MODEL` | `gpt-4o-mini` | Model used when an agent doesn't name one |
| `ZENTRA_SECRET_KEY` | (empty) | Encrypts per-agent keys and secrets. Empty → the platform refuses to store them. **Losing it makes stored keys unreadable** |
| `SANDBOX_RUNS_PER_CONTAINER` | 50 | Runs one container handles at once; the rest queue. They share 2 GB, so lower it for memory-heavy agents |
| `RUN_QUEUE_TIMEOUT_SECONDS` | 3600 | Longest a run may wait in the queue before failing |
| `CONTAINER_IDLE_TIMEOUT_SECONDS` | 1800 | Idle time before a container is stopped. Don't set 0 |
| `CONTAINER_START_TIMEOUT_SECONDS` | 180 | Longest wait for a container to start before falling back to a one-off sandbox |
| `SANDBOX_MAX_CONCURRENT` | 10 | Max one-off sandboxes at once (the fallback path) |
| `APP_WORKERS` | 2 | Processes for `app`. One process uses at most one CPU core; raise only on bigger hosts, max 5 |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | 5 / 10 | DB connections **per `app` process**. Total must stay under Postgres's 100 |
| `POSTGRES_*`, `DATABASE_URL`, `REDIS_URL` | compose values | Database and Redis addresses. Usually left alone |
| `SANDBOX_NETWORK`, `SANDBOX_GATEWAY_URL` | compose values | The containers' network and the only address they may call. The host must be `app` |
| `INTERNAL_REGISTRY_HOST`, `INTERNAL_REGISTRY_URL` | `localhost:6000`, `http://registry:5000` | The built-in registry as seen from the host and from inside compose |
| `ZENTRA_REGISTRY_AUTH_FILE` | empty (public images only) | Login file for pulling from a private external registry |

Details: [`.env.example`](.env.example) and [`specs/dispatcher-resources.md`](specs/dispatcher-resources.md).

## Ship an agent

Copy [`examples/my-agent/`](examples/my-agent/), edit `agent.py`. The Dockerfile is two lines:

```dockerfile
FROM zentra/agent-base:1.0
COPY agent.py /opt/zentra/app/agent.py
```

**Pushing is registering** — the repository name is the agent name, the tag is the version:

```bash
cd examples/my-agent
docker build -t localhost:6000/my-agent:1.0.0 .
docker push  localhost:6000/my-agent:1.0.0
```

The platform validates the image in a throwaway container. When the version shows `PUBLISHED` in the Console, call it:

```bash
curl -s -X POST "http://localhost:8000/v1/agents/$AGENT_ID/runs:invoke" \
  -H 'Content-Type: application/json' -d '{"input":{"question":"hello"}}'
```

For MCP, issue a key in the Console (`/console/mcp`) and tick the agent; it appears as the tool `my-agent__default`.
Rules, `ctx` features and common mistakes: [`specs/agent-development.md`](specs/agent-development.md).

## Testing

There is no unit test suite. Changes are verified by running them on the real stack.

```bash
make smoke             # end-to-end + isolation probes; expect 38 pass / 0 fail / 2 skipped
make clean-test-data   # afterwards, if you created test agents or runs
```

The two skips (kernel isolation, container escape) are not provided by this architecture and are never counted as
passes. Load tests: [`test/performance/`](test/performance/).

## Project layout

| Where | What it is |
|---|---|
| `compose.yaml` | Defines the five services and the two networks |
| `Makefile` | Shortcuts for common commands (`make help`) |
| `pyproject.toml` | Python dependencies of `app` and `worker` |
| `.env.example` | Optional settings template (LLM key, etc.); copy to `.env` |
| `CLAUDE.md` | Development rules for Claude Code |
| [`specs/`](specs/) | **The source of truth.** 12 documents, one per area; start at `overview.md`. Change the spec before the code |
| [`coordination/BOARD.md`](coordination/BOARD.md) | Task board: in progress, known defects, deliberately out of scope |
| [`apps/api/`](apps/api/) | The `app` service: REST, MCP, live event streams, the Console's files, and the Internal Gateway that containers call |
| `apps/api/routers/` | The HTTP endpoints: agents, runs, MCP keys, internal gateway |
| `apps/api/services/` | The logic behind them: registering versions, creating runs, LLM proxy, outbound HTTP, email, keys, tokens, work queue |
| `apps/api/migrations/` | Database schema as numbered SQL files (`make migrate`) |
| [`apps/worker/`](apps/worker/) | The `worker` service (Celery): dispatches runs, validates new versions, starts, stops and health-checks containers |
| [`apps/sandbox/`](apps/sandbox/) | The only code that talks to Docker: creates locked-down containers, enforces limits |
| [`apps/console/`](apps/console/) | The Console web UI: plain HTML/CSS/JS pages served by `app` |
| [`packages/sdk/`](packages/sdk/) | `zentra_sdk`, the library agent authors use (`@agent`, `ctx.llm()`, `ctx.http`…) |
| [`docker/`](docker/) | Image build files (`app`, `worker`, agent base image) and the registry config |
| [`examples/`](examples/) | Sample agents: `my-agent` (start here), `llm-agent` (calls an LLM), `stock-agent` (search loop), `probe-agent` (the platform's test agent) |
| [`scripts/`](scripts/) | Smoke test, test-agent setup, test-data cleanup, one-off legacy DB adoption |
| [`test/performance/`](test/performance/) | Locust load tests (which agents to hit: `targets.toml`), a fake LLM, and a memory sampler |
| [`monitoring/`](monitoring/) | Prometheus + Grafana, deployed separately from the platform |
| `.claude/` | Claude Code settings, the `builder` agent and the `/verify` command |

---

<a name="繁體中文"></a>

## 繁體中文

**Zentra 是一個 Agent 平台。** 你用 LangGraph 寫好 Python Agent，打包成 image、`docker push` 上平台。
平台把它凍結成不可變的版本、放在隔離容器裡執行，並自動開好 **REST API**。
也可以把 Agent 勾進平台**唯一的 MCP 接口（`/mcp`）**，讓 Claude、ChatGPT 這類 AI 應用當成工具來呼叫。

**交付條件：** 內部工具 · 單租戶 · 只跑信任的程式碼 · 一台主機跑 `docker compose`，五個服務
（`postgres` · `redis` · `registry` · `app` · `worker`）· 不用 Kubernetes · Console 是原生 HTML/CSS/JS、不用建置。
目前狀態：主要路徑已經可以從頭跑到尾；哪些驗過、哪些還沒，看 [`coordination/BOARD.md`](coordination/BOARD.md)。

### 五條規則（違反就是 bug）

| | 規則 |
|---|---|
| **P1** | 使用者的程式碼**永遠不在平台的行程裡跑**。worker 只派工，程式碼一律在容器裡執行 |
| **P2** | Agent 一律用 **LangChain + LangGraph**，最後都是一個 `StateGraph` |
| **P3** | 每個 Agent 都有 **REST API**，也能加入**唯一的 MCP 接口**，兩者從同一份 `entrypoints` 自動產生 |
| **P4** | **容器裡拿不到任何長期金鑰。** 容器只連得到 `app`，金鑰由 `app` 代為保管 |
| **P5** | **版本不可變**，用完整的 image digest 識別 |

### 啟動

```bash
make up                                   # 起五個服務 + base image + migration + 健康檢查
make help                                 # 所有指令
open http://localhost:8000/console/agents # Console
```

- 改了 `apps/api` 或 `apps/console`：`make rebuild-app`。改了 `apps/worker` 或 `apps/sandbox`：`docker compose up -d --build worker`。
- Registry 在主機的 **6000** port（macOS 的 AirPlay 佔了 5000）。
- 清空資料庫重來（**會刪掉所有 Agent、Run 和已註冊的 image**）：`docker compose down -v && make up`。

### 設定（環境變數）

都是選填。`cp .env.example .env` 之後改，再跑 `docker compose up -d` 生效。不填的話每一項都有能用的預設值。

**最常需要改的**

| 變數 | 預設 | 白話說明 |
|---|---|---|
| `ZENTRA_LLM_API_KEY` | （空） | 平台共用的 LLM 金鑰，給沒有自己金鑰的 Agent 用。不填的話，這些 Agent 呼叫 LLM 會失敗（`LLM_ERROR`） |
| `ZENTRA_LLM_BASE_URL` | OpenAI | 要打哪一家 LLM（只要是 OpenAI 相容的都可以） |
| `ZENTRA_LLM_DEFAULT_MODEL` | `gpt-4o-mini` | Agent 沒指定模型時用哪一個 |
| `ZENTRA_SECRET_KEY` | （空） | 加密每個 Agent 自己的金鑰和機密用的鑰匙。不填就不能存這些東西。**弄丟或換掉，已存的金鑰全部解不開** |

**效能與容量**

| 變數 | 預設 | 白話說明 |
|---|---|---|
| `SANDBOX_RUNS_PER_CONTAINER` | 50 | 一個 Agent 容器同時跑幾筆，多的排隊。這幾筆共用 2 GB 記憶體，Agent 很吃記憶體就調小 |
| `RUN_QUEUE_TIMEOUT_SECONDS` | 3600 | 排隊最多等幾秒，超過就算失敗 |
| `CONTAINER_IDLE_TIMEOUT_SECONDS` | 1800 | 容器沒人用多久就關掉，下次有人用再開。不要設 0（會永遠不關） |
| `CONTAINER_START_TIMEOUT_SECONDS` | 180 | 容器開機最多等幾秒，超過就改用一次性容器 |
| `SANDBOX_MAX_CONCURRENT` | 10 | 一次性容器（備用路線）同時最多幾個 |
| `APP_WORKERS` | 2 | `app` 開幾個行程。一個行程最多用一顆 CPU；4 核心的主機 2 就夠，主機大才調高，最多 5 |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | 5 / 10 | **每個** `app` 行程的資料庫連線數。全部加起來要低於 Postgres 的上限 100，通常不用動 |

**通常不用動的**

| 變數 | 預設 | 白話說明 |
|---|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `zentra` | 資料庫的帳號、密碼、名稱 |
| `DATABASE_URL` / `REDIS_URL` | compose 內的位址 | `app` 和 `worker` 連資料庫、Redis 的位址 |
| `SANDBOX_NETWORK` | `zentra-sandbox-net` | Agent 容器所在的隔離網段 |
| `SANDBOX_GATEWAY_URL` | `http://app:8000/internal` | Agent 容器唯一能連的位址。主機名一定要是 `app`，寫錯的話 Run 會直接失敗 |
| `INTERNAL_REGISTRY_HOST` / `INTERNAL_REGISTRY_URL` | `localhost:6000` / `http://registry:5000` | 內建 registry 的位址：從主機看、從 compose 裡面看 |
| `ZENTRA_REGISTRY_AUTH_FILE` | 空的登入檔（只能拉公開 image） | 要從需要登入的外部 registry 拉 image 時，登入資訊放哪個檔案 |
| `ZENTRA_ENV` | `dev` | 執行環境的標記 |

每一項的細節寫在 [`.env.example`](.env.example) 和 [`specs/dispatcher-resources.md`](specs/dispatcher-resources.md)。

### 發布一個 Agent

複製 [`examples/my-agent/`](examples/my-agent/)，改 `agent.py`。Dockerfile 只要兩行：

```dockerfile
FROM zentra/agent-base:1.0
COPY agent.py /opt/zentra/app/agent.py
```

**push 就是註冊**：repository 名稱就是 Agent 名稱，tag 就是版號。

```bash
cd examples/my-agent
docker build -t localhost:6000/my-agent:1.0.0 .
docker push  localhost:6000/my-agent:1.0.0
```

平台會在一次性容器裡驗證這個 image。Console 上看到版本變成 `PUBLISHED` 就能呼叫：

```bash
curl -s -X POST "http://localhost:8000/v1/agents/$AGENT_ID/runs:invoke" \
  -H 'Content-Type: application/json' -d '{"input":{"question":"哈囉"}}'
```

要走 MCP：在 Console 的 `/console/mcp` 發一把 key、勾選這個 Agent，它就會以 `my-agent__default` 這個工具名稱出現。
寫 Agent 的規則、`ctx` 能做什麼、常見錯誤：[`specs/agent-development.md`](specs/agent-development.md)。

### 測試

沒有單元測試套件，改動靠實際跑起來驗證。

```bash
make smoke             # 從頭跑一次 + 隔離檢查，預期 38 通過 / 0 失敗 / 2 跳過
make clean-test-data   # 有建測試 Agent 或 Run 的話，收尾時跑
```

跳過的兩項（核心隔離、容器逃逸）是這套架構本來就不提供的，永遠不算通過。壓力測試看 [`test/performance/`](test/performance/)。

### 資料夾與檔案說明

**根目錄**

| 檔案 | 做什麼 |
|---|---|
| `compose.yaml` | 定義五個服務和兩個網段，`make up` 就是照它起 |
| `Makefile` | 常用指令的捷徑，`make help` 看全部 |
| `pyproject.toml` | `app` 和 `worker` 要裝的 Python 套件 |
| `.env.example` | 選用設定的範本（LLM 金鑰等），複製成 `.env` 再填 |
| `CLAUDE.md` | 給 Claude Code 的開發規則 |

**[`specs/`](specs/) —— 規格，所有行為以這裡為準。要改行為先改規格，再改程式。**

| 檔案 | 寫的是什麼 |
|---|---|
| `overview.md` | 整體架構、五條規則、名詞。**從這份開始讀** |
| `api.md` | 對外 API：有哪些網址、欄位、錯誤碼 |
| `dispatch.md` | 一筆 Run 從建立到結束怎麼走、狀態怎麼變 |
| `agent-development.md` | 怎麼寫 Agent、`ctx` 能做什麼、常見錯誤 |
| `agent-registry.md` | Agent 和版本怎麼註冊、取代、刪除 |
| `dispatcher-resources.md` | 主機資源、各種上限、可調的設定 |
| `container-orchestration.md` | 容器怎麼建、怎麼隔離、死掉怎麼辦 |
| `gateway.md` | 容器唯一能連的 Internal Gateway：代打 LLM、對外連線、寄信、稽核 |
| `mcp.md` | MCP 接口 |
| `postgresql.md` | 資料表 |
| `redis.md` | Redis 裡存了哪些東西 |
| `console-ui.md` | Console 畫面 |

**[`apps/`](apps/) —— 平台本身**

| 位置 | 做什麼 |
|---|---|
| `apps/api/` | **`app` 服務**。所有 HTTP 都進這裡：REST、MCP、即時事件、Console 網頁、容器呼叫的 Internal Gateway |
| `apps/api/main.py` | 程式進入點，把下面的路由組起來 |
| `apps/api/mcp.py` | MCP 接口 `/mcp`：列出工具、呼叫 Agent |
| `apps/api/config.py` | 所有可調設定（讀環境變數） |
| `apps/api/enums.py` · `errors.py` | Run 狀態、錯誤碼的唯一定義；統一的錯誤格式 |
| `apps/api/schemas.py` · `rows.py` | API 請求/回應的格式；資料表每一列的格式 |
| `apps/api/db.py` · `deps.py` · `ids.py` · `static.py` · `tasks.py` | 資料庫連線、共用的請求處理、產生 ID、送出 Console 網頁、把工作交給 worker |
| `apps/api/routers/` | 對外網址：`agents.py`（Agent 與版本）、`runs.py`（執行 Agent）、`mcp_keys.py`（MCP key）、`internal.py`（容器專用的 Gateway） |
| `apps/api/services/` | 網址背後的實際邏輯，見下表 |
| `apps/api/migrations/` | 資料庫結構，依編號的 SQL 檔逐一套用（`make migrate`） |
| `apps/worker/` | **`worker` 服務**（Celery）。`tasks/run.py` 派工、`tasks/build.py` 驗證新版本、`tasks/container.py` 啟停容器與每 60 秒的健康檢查和清掃 |
| `apps/sandbox/` | **唯一碰 Docker 的程式**。`drivers/docker_driver.py` 建立鎖死權限的容器，`manager.py` 管同時能開幾個 |
| `apps/console/` | **Console 網頁**。`*.html` 是各頁面，`static/js/pages/` 是每頁的程式，`static/js/components/` 是頁面上的區塊，`static/css/` 是樣式 |

**`apps/api/services/` 各檔案**

| 檔案 | 做什麼 |
|---|---|
| `agents.py` | Agent 和版本的資料、產生 API 文件與 MCP 工具定義 |
| `registry_events.py` | 收到 `docker push` 通知 → 註冊版本 |
| `validation.py` · `validationtoken.py` | 存新版本的驗證結果；驗證容器用的臨時通行證 |
| `runs.py` | 建立 Run 並派工、存結果 |
| `work.py` | 常駐容器的工作佇列（在 Redis 排隊） |
| `containers.py` · `containertoken.py` | 常駐容器的狀態；容器取工作用的通行證 |
| `runtoken.py` | 每筆 Run 專屬的臨時通行證，Run 結束就失效 |
| `events.py` · `streaming.py` | 記錄 Run 的事件；即時推給瀏覽器（SSE） |
| `llm.py` | 代替容器呼叫 LLM：換上真金鑰、記錄用量 |
| `egress.py` · `egress_rules.py` · `egress_audit.py` | 代替容器連外網：白名單、實際代打、紀錄查詢 |
| `notify.py` | 代替容器寄信 |
| `agent_credentials.py` · `agent_secrets.py` · `agent_settings.py` · `secrets.py` | 每個 Agent 自己的 LLM 金鑰、機密、設定；加密保存 |
| `agent_api_keys.py` · `mcp_keys.py` · `usage.py` | 給下游應用的 API key、MCP key、各 key 的用量 |
| `pagination.py` | 列表分頁 |

**其他資料夾**

| 位置 | 做什麼 |
|---|---|
| [`packages/sdk/`](packages/sdk/) | **`zentra_sdk`**，寫 Agent 的人用的套件：`@agent` 宣告、`ctx.llm()`、`ctx.http`、`ctx.send_email()`。`image_runtime.py` 是容器裡實際跑 Agent 的程式 |
| [`docker/`](docker/) | 映像檔的建置檔：`app`、`worker`、Agent 的 base image；以及 registry 的設定 |
| [`examples/my-agent/`](examples/my-agent/) | **寫 Agent 從這裡開始**。有分支的文字分析 Agent |
| [`examples/llm-agent/`](examples/llm-agent/) | 呼叫 LLM 的範例，金鑰留在平台上 |
| [`examples/stock-agent/`](examples/stock-agent/) | 搜尋 → 判斷夠不夠 → 再搜尋的循環 |
| [`examples/probe-agent/`](examples/probe-agent/) | 平台自己的測試 Agent，冒煙測試和壓測都打它 |
| [`scripts/`](scripts/) | `verify-zen000.sh` 冒煙測試 · `fixture.sh` 註冊測試 Agent · `clean-test-data.sh` 清測試資料 · `adopt-legacy-schema.sh` 舊資料庫一次性轉換 |
| [`test/performance/`](test/performance/) | Locust 壓力測試。**要壓哪些 Agent 寫在 `targets.toml`**；另有假 LLM（`mock_llm.py`）、記憶體取樣（`memwatch.sh`） |
| [`monitoring/`](monitoring/) | Prometheus + Grafana 監控，跟平台分開部署 |
| [`coordination/BOARD.md`](coordination/BOARD.md) | 任務看板：進行中、已知問題、刻意不做的事 |
| `.claude/` | Claude Code 的設定、`builder` 代理、`/verify` 驗收指令 |

---

## License

_License not yet chosen._
