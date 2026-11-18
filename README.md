<div align="center">

# Zentra

**An agent platform: push your agent as a container image, get an isolated runtime, a REST API, and a slot on one shared MCP endpoint.**

[繁體中文](#繁體中文) · [Specs](specs/)

</div>

---

You write a Python agent with LangGraph, package it as an image and `docker push` it to the platform. The platform
freezes it as an immutable version, runs it in an isolated container, and exposes it as a **REST API**. Agents can
also be ticked into the platform's **single MCP endpoint (`/mcp`)**, where each one becomes a tool that AI apps such
as Claude or ChatGPT can call.

Internal tool · single tenant · trusted code only · one host running `docker compose` with five services
(`postgres` · `redis` · `registry` · `app` · `worker`) · the console is plain HTML/CSS/JS with no build step.

## Run it

```bash
docker build -t zentra/agent-base:1.0 -f docker/agent-base.Dockerfile .   # agent base image
docker compose up -d --build                                              # 5 services
docker compose exec -T app python -m apps.api.migrations                  # DB migrations
curl -s localhost:8000/healthz
open http://localhost:8000/console/agents                                 # Console
```

- After changing `apps/api` or `apps/console`: `docker compose up -d --build app`. After `apps/worker` or `apps/sandbox`: `docker compose up -d --build worker`.
- Logs: `docker compose logs -f app` (or `worker`). Stop: `docker compose down`.
- The registry is on host port **6000** (macOS AirPlay takes 5000).
- Start over with an empty database (**deletes all agents, runs and registered images**): `docker compose down -v`, then the commands above.

## Settings (environment variables)

Optional. `cp .env.example .env`, edit, then `docker compose up -d` to apply. Every value has a working default; see [`.env.example`](.env.example).

| Variable | Default | What it controls |
|---|---|---|
| `ZENTRA_LLM_API_KEY` | (empty) | Platform-wide LLM key for agents without their own. Empty → LLM calls fail with `LLM_ERROR` |
| `ZENTRA_LLM_BASE_URL` | OpenAI | Which LLM provider to call (any OpenAI-compatible endpoint) |
| `ZENTRA_LLM_DEFAULT_MODEL` | `gpt-4o-mini` | Model used when an agent doesn't name one |
| `ZENTRA_SECRET_KEY` | (empty) | Encrypts per-agent keys and secrets. Empty → the platform refuses to store them. **Losing it makes stored keys unreadable** |
| `SANDBOX_RUNS_PER_CONTAINER` | 50 | Runs one container handles at once; they share 2 GB |
| `RUN_QUEUE_TIMEOUT_SECONDS` | 3600 | Longest a run may wait in the queue before failing |
| `CONTAINER_IDLE_TIMEOUT_SECONDS` | 1800 | Idle time before a container is stopped. Don't set 0 |
| `CONTAINER_START_TIMEOUT_SECONDS` | 180 | Longest wait for a container to start before falling back to a one-off sandbox |
| `SANDBOX_MAX_CONCURRENT` | 10 | Max one-off sandboxes at once |
| `APP_WORKERS` | 2 | Processes for `app`, max 5 |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | 5 / 10 | DB connections **per `app` process**. Total must stay under Postgres's 100 |

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

When the version shows `PUBLISHED` in the Console, call it:

```bash
curl -s -X POST "http://localhost:8000/v1/agents/$AGENT_ID/runs:invoke" \
  -H 'Content-Type: application/json' -d '{"input":{"question":"hello"}}'
```

For MCP, issue a key in the Console (`/console/mcp`) and tick the agent; it appears as the tool `my-agent__default`.
Rules and `ctx` features: [`specs/agent.md`](specs/agent.md).

## Project layout

| Where | What it is |
|---|---|
| `compose.yaml` | The five services and two networks |
| `pyproject.toml` | Python dependencies of `app` and `worker` |
| [`specs/`](specs/) | `api.md` · `mcp.md` · `gateway.md` · `agent.md` · `db.md` |
| [`apps/api/`](apps/api/) | `app`: REST, MCP, SSE, the Console's files, and the Internal Gateway; `migrations/` holds the schema |
| [`apps/worker/`](apps/worker/) | `worker` (Celery): dispatches runs, validates versions, manages containers |
| [`apps/sandbox/`](apps/sandbox/) | The only code that talks to Docker |
| [`apps/console/`](apps/console/) | Console web UI |
| [`packages/sdk/`](packages/sdk/) | `zentra_sdk`, the library agent authors use |
| [`docker/`](docker/) | Image build files and registry config |
| [`examples/`](examples/) | Sample agents; `my-agent` is the starting point |
| [`test/performance/`](test/performance/) | Locust load tests |
| [`monitoring/`](monitoring/) | Prometheus + Grafana, deployed separately |

---

<a name="繁體中文"></a>

## 繁體中文

**Zentra 是一個 Agent 平台。** 用 LangGraph 寫好 Python Agent，打包成 image、`docker push` 上平台。
平台把它凍結成不可變的版本、在隔離容器裡執行，自動開好 **REST API**；也可以勾進平台唯一的 **MCP 接口（`/mcp`）** 給 AI 應用當工具。

內部工具 · 單租戶 · 只跑信任的程式碼 · 一台主機 `docker compose` 跑五個服務 · Console 原生 HTML/CSS/JS 無建置。

### 啟動

```bash
docker build -t zentra/agent-base:1.0 -f docker/agent-base.Dockerfile .   # Agent 的 base image
docker compose up -d --build                                              # 起五個服務
docker compose exec -T app python -m apps.api.migrations                  # 資料庫 migration
curl -s localhost:8000/healthz
open http://localhost:8000/console/agents                                 # Console
```

- 改了 `apps/api` 或 `apps/console`：`docker compose up -d --build app`；改了 `apps/worker` 或 `apps/sandbox`：`docker compose up -d --build worker`。
- 看 log：`docker compose logs -f app`（或 `worker`）。停止：`docker compose down`。
- Registry 在主機 **6000** port。
- 清空重來（**會刪掉所有 Agent、Run、image**）：`docker compose down -v`，再跑上面的指令。

### 設定

選填。`cp .env.example .env` 後修改，`docker compose up -d` 生效。變數說明見上方英文表格與 [`.env.example`](.env.example)。

### 發布 Agent

複製 [`examples/my-agent/`](examples/my-agent/)，改 `agent.py`，然後：

```bash
cd examples/my-agent
docker build -t localhost:6000/my-agent:1.0.0 .
docker push  localhost:6000/my-agent:1.0.0      # push 就是註冊
```

Console 上版本變 `PUBLISHED` 就能呼叫。寫法與規則看 [`specs/agent.md`](specs/agent.md)。

### 規格

| 檔案 | 內容 |
|---|---|
| [`specs/api.md`](specs/api.md) | 對外 REST API |
| [`specs/mcp.md`](specs/mcp.md) | MCP 接口 |
| [`specs/gateway.md`](specs/gateway.md) | 容器專用的 Internal Gateway：LLM、對外連線、寄信 |
| [`specs/agent.md`](specs/agent.md) | 寫 Agent、發布、執行流程 |
| [`specs/db.md`](specs/db.md) | PostgreSQL 與 Redis |

---

## License

_License not yet chosen._
