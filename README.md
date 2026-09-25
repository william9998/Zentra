<div align="center">

# Zentra

**An agent platform where every agent runs in an isolated sandbox and ships with both a REST API and an MCP endpoint.**

[繁體中文](#繁體中文) · [Architecture](docs/architecture/detailed-architecture.md) · [Spec](specs/SPEC.md) · [ADRs](specs/adr/)

</div>

---

Zentra lets you upload your own Python agent (LangGraph), freezes it into an immutable version, runs it inside an
isolated sandbox, and automatically publishes it behind **two front doors: a REST API and an MCP endpoint** —
generated from a single definition so they can never drift apart.

> **Status: under implementation.** The walking skeleton runs end to end — upload-free builtin agent,
> OCI image registration, sandboxed execution, REST + MCP and the Run observation console. Nothing has been
> accepted as `DONE` beyond the console slice; [`coordination/BOARD.md`](coordination/BOARD.md) is the
> authority on what is verified and what is still open.
>
> **Scoped down in [ADR-0005](specs/adr/ADR-0005-mvp-single-host-scope.md):** internal tool, single tenant,
> trusted code, 8-week MVP. Deployment is **one host running `docker compose`** — no Kubernetes.
> The console is **plain HTML + CSS + ES modules with no build step**
> ([ADR-0006](specs/adr/ADR-0006-vanilla-frontend.md)), served by the API itself — **four services total**.
> The five commitments below are unchanged; the infrastructure under them is a quarter of the size.

## Why this exists

Running user code is the hard part of any agent platform. Most of the design work here goes into one question: **what stops an uploaded agent from touching platform credentials, reaching the data layer, or calling out to the internet?**

The answer is five commitments that the rest of the architecture follows from.
ADR-0005 relaxed the *threat model* to trusted code — **it did not relax these five.**

| | Commitment |
|---|---|
| **P1** | User code **never executes inside a platform process**. Celery workers only dispatch and observe — every `exec` happens in a sandbox. |
| **P2** | Agents are **always LangChain + LangGraph**. One framework means streaming, event hooks and tracing are written once. |
| **P3** | Every agent gets **both an API and an MCP interface**, generated from the same `entrypoints` definition. |
| **P4** | **No long-lived credential ever enters a sandbox.** The sandbox network has no route out; its only peer is an **Internal Gateway** that holds the secrets. |
| **P5** | **Published versions are immutable.** `agent@semver` is identified by the bundle's SHA-256; its directories are frozen read-only on publish. |

## Architecture

![Zentra system architecture](docs/architecture/zentra-architecture.svg)

One host, one `docker compose`, four long-running services — plus a sandbox container created per Run and destroyed
when it ends. The sandbox sits on a network with **no route out**; its only peer is the `api` service's Internal
Gateway, which holds the LLM key, meters tokens and collects events. The console has no container of its own: it is
static HTML/CSS/JS served by `api`, calling the same `/v1` API everyone else calls.

```
L0  Access + Control   api (FastAPI) — REST · MCP · SSE · Console static files · Registry · Internal Gateway
L1  Dispatch           worker (Celery, Redis broker) — q.run · q.build
L2  Execution          Sandbox Manager → Sandbox (Docker, one per Run)
L3  Data               PostgreSQL · Redis · local volume (bundles / venvs)
```

**[→ 11 detailed diagrams](docs/architecture/detailed-architecture.md)** covering the publish flow, request lifecycle, sandbox isolation model, Celery topology, agent runtime, data model and deployment.

### The two hard parts

**Sandbox isolation** ([ADR-0001](specs/adr/ADR-0001-sandbox-isolation.md), scoped by [ADR-0005](specs/adr/ADR-0005-mvp-single-host-scope.md)) — four Docker-native layers, on the assumption that each one can be breached: an internal-only network, dropped capabilities and a non-root user, cgroup limits, and a read-only filesystem, plus an SDK interception layer. Sandboxes are never reused across runs. **There is no gVisor**, so `capabilities()` reports `l2_kernel_isolation: false` and the matching security tests are marked `skipped`, never `passed` — this tier fits trusted code, not a public multi-tenant platform.

**Celery ↔ LangGraph boundary** ([ADR-0002](specs/adr/ADR-0002-celery-langgraph-boundary.md)) — one rule: *crosses machines, needs queueing, or scales independently → Celery; just the next step inside one agent → a LangGraph node.* One run is one Celery task, however many nodes the graph has.

## Repository layout

| Path | What's in it |
|---|---|
| [`specs/`](specs/) | **Single source of truth.** Master spec, API & UI contracts, glossary, ADRs, feature specs |
| [`docs/architecture/`](docs/architecture/) | Mermaid diagrams and design deep-dives |
| [`coordination/`](coordination/) | Task board, handoff notes, spec-drift audits |
| [`.claude/agents/`](.claude/agents/) | Three role agents — architect, builder, reviewer |
| [`.claude/commands/`](.claude/commands/) | `/task` `/verify` `/drift` `/handoff` |
| [`.codex/agents/`](.codex/agents/) | The same three roles registered for Codex, pointing at the definitions above |
| [`.agents/skills/`](.agents/skills/) | The same four workflows registered for Codex |
| [`docs/codex.md`](docs/codex.md) | Codex setup, invocation examples, and collaboration limits |

## Built by a team of AI agents

This repository is developed by role agents under Claude Code and Codex, both reading the same
definitions. Three roles, not a bureaucracy:

- **`architect`** owns the spec, the contracts and the ADRs — called only when a contract has to change
- **`builder`** writes all the product code: backend, Console and sandbox
- **`reviewer`** writes the acceptance criteria, runs the tests and audits the code for drift away from the spec

The process is tiered on purpose: a small change is made directly, with no subagent and no handoff
note. Only work that changes [`api-contract.md`](specs/api-contract.md), needs a new ADR, or touches
the sandbox and credential boundary goes through the full architect → builder → reviewer path.

What holds it together is **contract-first** development — frontend and backend both target
[`api-contract.md`](specs/api-contract.md) instead of waiting on each other — plus a
[task board](coordination/BOARD.md) and, for the larger pieces, handoff notes.

The rules are in [`AGENTS.md`](AGENTS.md), which is deliberately short.

## Stack

Python 3.12 · FastAPI · Celery (Redis broker) · LangChain + LangGraph · PostgreSQL · Redis · Docker · plain HTML/CSS/JS (no build step)

## Getting started

To understand the project:

1. [`specs/SPEC.md`](specs/SPEC.md) — what it is and the five commitments
2. [`docs/architecture/detailed-architecture.md`](docs/architecture/detailed-architecture.md) — how it fits together
3. [`specs/adr/`](specs/adr/) — why it's built this way rather than some other way
4. [`coordination/BOARD.md`](coordination/BOARD.md) — what is actually built, what is still open

Local and production are the same `docker compose` stack — there is no separate deployment target. See
[ADR-0005](specs/adr/ADR-0005-mvp-single-host-scope.md) for what was cut and why, and the
[runtime notes](docs/architecture/07-local-dev-sandbox.md) for how sandboxes are actually launched.

## Running the stack

```bash
docker compose up -d --build                     # postgres · redis · registry · api · worker
docker build -t zentra/agent-base:1.0 -f docker/agent-base.Dockerfile .
docker compose exec api alembic upgrade head
```

Two things compose does **not** do for you:

- **The base image.** `zentra/agent-base:1.0` is what your own agent images build `FROM`. It belongs to
  no compose service — nothing runs it — so compose has no place to build it from. Rebuild it after a
  change under `packages/sdk/`; images already published are unaffected, since their contents are frozen.
- **Migrations.** Nothing runs Alembic at startup.

A **Run uses the agent's own image**, pulled by the digest registered for that version. There is no
shared runtime container in that path. The one exception is the built-in `builtin-echo` fixture left over
from ZEN-000, which still runs from `zentra/agent-runtime:dev` (`SANDBOX_IMAGE`) and is what the smoke
test below exercises. Build it only if you intend to run that script:

```bash
docker build -t zentra/agent-runtime:dev -f docker/agent-runtime.Dockerfile .
```

Retiring that fixture is [tracked on the board](coordination/BOARD.md); it is load-bearing for the smoke
test and nine test files, so it is a task of its own rather than a deletion.

The registry publishes on **host port 6000**, not the 5000 registries usually take: macOS runs AirPlay
Receiver on `*:5000`, which shadows it. Inside the container it is still 5000 — only the host side moved
([ADR-0009](specs/adr/ADR-0009-internal-registry-image-upload.md) R3). Check it with the header, not just
reachability, since anything else on that port would also answer:

```bash
curl -sI http://127.0.0.1:6000/v2/ | grep -i docker-distribution-api-version
```

Both networks — including the internal-only `zentra-sandbox-net` — are declared in `compose.yaml`, so no
`docker network create` is needed. `.env` is optional; nothing in the current scope requires a secret.

```bash
curl -s localhost:8000/healthz                   # {"status":"ok"}
open http://localhost:8000/console/agents        # Console
```

The API image carries the console's static files, so **a console change needs
`docker compose up -d --build api`** before the browser can see it.

## Ship an agent

The whole loop, end to end. Every command below was run against this stack.

### 1. Write `agent.py`

```python
from typing import TypedDict
from langgraph.graph import END, START, StateGraph
from zentra_sdk import agent

class State(TypedDict, total=False):
    question: str
    answer: str

@agent(
    name="my-agent",
    version="1.0.0",
    entrypoints=[{
        "name": "default",
        "description": "What this entrypoint does",
        "input_schema":  {"type": "object", "required": ["question"],
                          "properties": {"question": {"type": "string"}}},
        "output_schema": {"type": "object", "required": ["answer"],
                          "properties": {"answer": {"type": "string"}}},
    }],
)
def build(ctx) -> StateGraph:
    graph = StateGraph(State)

    async def node(state: State) -> dict:
        return {"answer": f"got: {state['question']}"}

    graph.add_node("node", node)
    graph.add_edge(START, "node")
    graph.add_edge("node", END)
    return graph
```

`entrypoints` is the only schema source there is. REST input validation, the MCP `tools/list` response
and the per-agent OpenAPI document are all generated from it (P3), so changing it means publishing a new
version.

The module must be importable as `agent` — the filename can differ, the module name cannot
([ADR-0003](specs/adr/ADR-0003-agent-packaging-dual-interface.md)).

### 2. Write the Dockerfile

```dockerfile
FROM zentra/agent-base:1.0
COPY agent.py /opt/zentra/app/agent.py
```

The base image already carries the SDK, its dependencies, the `io.zentra.image-contract` label and
uid 65534. Writing the Dockerfile by hand instead is fine — see
[`examples/demo-echo/Dockerfile`](examples/demo-echo/Dockerfile), which spells out every contract
requirement — but then the dependencies are yours to get right.

### 3. Build and push

```bash
docker build -t localhost:6000/my-agent:1.0.0 .
docker push  localhost:6000/my-agent:1.0.0
```

The last line of the push output carries the digest you need:

```
1.0.0: digest: sha256:58b16bb2… size: 2407
```

That is the **registry manifest digest**. The sha256 `docker build` prints is the local image ID, a
different identifier that cannot be substituted for it. If you lose the push output:

```bash
docker inspect --format='{{index .RepoDigests 0}}' localhost:6000/my-agent:1.0.0
```

Tag with the semver you are about to register. Nothing enforces it, but it keeps every registered
manifest reachable by a tag, which matters for the garbage-collection trap in
[`docs/operations.md`](docs/operations.md).

An external registry (GHCR, Docker Hub, your own) works exactly the same way. The platform does not care
which one, only that it can pull the digest.

### 4. Create the Agent and register the version

In the Console at `http://localhost:8000/console/agents`, or over the API:

```bash
AID=$(curl -s -X POST http://localhost:8000/v1/agents   -H 'Content-Type: application/json'   -d '{"name":"my-agent","description":"..."}' | jq -r .agent_id)

curl -s -X POST "http://localhost:8000/v1/agents/$AID/versions"   -H 'Content-Type: application/json'   -d "{\"semver\":\"1.0.0\",\"image_ref\":\"localhost:6000/my-agent@sha256:58b16bb2…\"}"
```

The response is `BUILDING`. In the background the platform pulls the image by digest, inspects it, and
runs `zentra-agent validate` **inside a throwaway sandbox** to extract the entrypoint schemas — user code
is never imported by the platform (P1). Poll until it settles:

```bash
curl -s "http://localhost:8000/v1/agents/$AID/versions/1.0.0" | jq .status
```

`PUBLISHED` means it is runnable. `FAILED` puts the validation container's full traceback in
`error.message`; that is the only debugging signal you get, so read it rather than guessing.

Only digests are accepted. A tag is rejected with `VALIDATION_ERROR`, and re-registering a semver that
already exists returns `409` — published versions are immutable (P5), so a change means a new semver.

### 5. Run it

```bash
# REST, synchronous, 60s ceiling
curl -s -X POST "http://localhost:8000/v1/agents/$AID/runs:invoke" \
  -H 'Content-Type: application/json' \
  -d '{"entrypoint":"default","input":{"question":"hello"}}'

# Asynchronous, with the event stream
RID=$(curl -s -X POST "http://localhost:8000/v1/agents/$AID/runs" \
  -H 'Content-Type: application/json' \
  -d '{"entrypoint":"default","input":{"question":"hello"}}' | jq -r .run_id)
curl -sN "http://localhost:8000/v1/runs/$RID/events"

# MCP — same agent, same schema, generated from the same entrypoints
curl -s -X POST "http://localhost:8000/mcp/agents/$AID" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"name":"default","arguments":{"question":"hello"}}}'
```

The stream runs `QUEUED → SCHEDULING → RUNNING → node.start → node.end → SUCCEEDED`. Each Run gets its own
sandbox container, which is destroyed when the Run ends and never reused.

### Three ways to get it wrong

All three surface as `BUILD_FAILED` with the traceback in `error.message`:

| Mistake | What happens |
|---|---|
| `def build()` | Rejected by an arity check that names the signature the contract wants. `build` takes the context. |
| Returning a `.compile()`d graph | The platform compiles it itself to inject the event hooks. Compile it yourself and the Run observation page has no `node.start` / `node.end` to show. |
| A hand-written Dockerfile missing `jsonschema` | A startup check names what is missing. The base image avoids this. |

### What the sandbox deliberately does not have

Do not look for these in your agent — their absence is the point (P4):

- Any API key, LLM credential or registry credential
- PostgreSQL or Redis
- Outbound network

The sandbox sits on a network with no route out; its only peer is the platform's Internal Gateway, which
holds the credentials on its behalf. Calling an LLM goes through `ctx.llm`, which **is not implemented
yet** (ZEN-002) — so today an agent can only do work that needs no external resource: computation, data
transformation, graph control flow.

### Publishing a new version

Build, push, register a new semver. The old version keeps running exactly as before; that is what
immutability buys. Registering over an existing semver is a `409`, and the registry has deletion disabled,
so a published version cannot be pulled out from under itself.

## Testing

### Smoke test: the ZEN-000 acceptance script

```bash
bash scripts/verify-zen000.sh
```

One end-to-end pass plus the isolation probes: SSE events, sandbox lifecycle, the four isolation layers, and
the contract error paths. Expect `通過 27 ／ 失敗 0 ／ 跳過 2`. The two skips are gVisor kernel isolation and
container escape — this architecture does not provide them, so they are reported as **skipped, never passed**
([ADR-0005](specs/adr/ADR-0005-mvp-single-host-scope.md)). Needs the stack up, migrations applied and the
runtime image built.

### Test suites

The host interpreter has no application dependencies, so tests run either through `uv` or inside the `api`
image on the compose network:

```bash
# with uv on the host
uv sync --extra dev && uv run pytest tests/zen006a -q

# without uv — same suite, inside the api image, on the compose network
docker run --rm --network zentra-net -v "$PWD:/srv" -w /srv \
  -e PYTHONPATH=/srv \
  -e ZENTRA_TEST_BASE_URL=http://api:8000 \
  -e ZENTRA_TEST_DB_URL=postgresql+asyncpg://zentra:zentra@postgres:5432/zentra \
  zentra-api:latest \
  sh -c "pip install -q pytest pytest-asyncio anyio && pytest tests/zen006a -q"
```

| Suite | Covers | Needs |
|---|---|---|
| [`tests/zen000`](tests/zen000) | Run lifecycle, sandbox driver, Internal Gateway, worker recovery, cold-start benchmark | `ZENTRA_TEST_BASE_URL`, `ZENTRA_TEST_DB_URL`; the driver tests need the `docker` CLI |
| [`tests/zen006a`](tests/zen006a) | Console source guards, live routes, headless-Chrome E2E | the guards need nothing; routes need `ZENTRA_TEST_BASE_URL`; the E2E needs `node` + Chrome |
| [`tests/zen008`](tests/zen008) | OCI registration, MCP facade, sandbox security, Console E2E | a registry on `:5010`, the `docker` CLI, `node` + Chrome |

**Run anything that touches the database from inside the compose network.** A Postgres running on the host
takes `127.0.0.1:5432` and masks the container's, so host-side tests silently connect to the wrong database —
that is what the `ZENTRA_TEST_DB_URL` skips exist to prevent.

Tests that only audit source files need no service and no application dependencies:

```bash
python3 -m pytest tests/zen006a/test_console_source_contract.py -q
```

If that one fails on the contract-version assertion, it is working as designed: `api-contract.md` has moved
and the console has not been re-checked against the new version yet. Fix the console (or confirm nothing
changed) and update the pin — do not just bump the string. Open findings live in
[`coordination/BOARD.md`](coordination/BOARD.md).

---

<a name="繁體中文"></a>

## 繁體中文

**Zentra 是一個 Agent 平台**：開發者上傳自己的 Python 程式碼（LangGraph），
平台把它凍結成不可變版本、在**隔離沙盒**中執行，並自動對外開放 **REST API** 與 **MCP** 兩種接口——
兩者由同一份 `entrypoints` 定義生成，因此永遠不會不一致。

> **目前狀態：實作中。** 貫穿骨架已經跑得通——內建 Agent、OCI image 註冊、沙盒執行、
> REST + MCP，以及 Run 觀測 Console。除了 Console 切片之外**都還沒被驗收為 `DONE`**，
> 哪些驗過、哪些還開著，以 [`coordination/BOARD.md`](coordination/BOARD.md) 為準。
>
> **已依 [ADR-0005](specs/adr/ADR-0005-mvp-single-host-scope.md) 降維：** 內部工具、單租戶、信任的程式碼、八週 MVP。
> 部署是**一台主機跑 `docker compose`**，不用 Kubernetes。
> 前端再依 [ADR-0006](specs/adr/ADR-0006-vanilla-frontend.md) 改為**原生 HTML + CSS + ES modules，零建置**，
> 由 API 自己服務 —— compose 是**五個服務**（[ADR-0009](specs/adr/ADR-0009-internal-registry-image-upload.md)
> 的內部 Registry 是第五個）。
> 下面五條立場一條沒動，但底下的基礎設施只剩四分之一。

### 為什麼這樣設計

執行使用者程式碼是 Agent 平台最難的部分。整份設計都在回答一個問題：
**要怎麼確保上傳的 Agent 摸不到平台憑證、碰不到資料層、打不了外網？**

答案是五條核心立場，其餘架構都是它們的推論：
（威脅模型隨 ADR-0005 降級為「信任的程式碼」，但**這五條沒有跟著降**）

| | 立場 |
|---|---|
| **P1** | 使用者程式碼**永遠不在平台行程內執行**。Celery worker 只派工與監看，`exec` 一律在沙盒。 |
| **P2** | Agent 框架**統一 LangChain + LangGraph**。收斂成一種，串流、事件 hook、觀測才只要寫一次。 |
| **P3** | 每個 Agent **天生就有 API + MCP 兩個門面**，由同一份定義生成。 |
| **P4** | **沙盒內拿不到任何長期憑證**。沙盒網段沒有對外路由，唯一對端是代持憑證的 **Internal Gateway**。 |
| **P5** | **發布的版本不可變**。`agent@semver` 以 bundle 的 SHA-256 識別，發布後目錄設唯讀。 |

### 架構圖

![Zentra 系統架構](docs/architecture/zentra-architecture.svg)

一台主機、一個 `docker compose`、**五個**長駐服務
（postgres · redis · registry · api · worker），
加上**每個 Run 動態建立、結束即銷毀**的沙盒容器。
沙盒掛在一個**沒有對外路由**的網段上，唯一的對端是 `api` 服務的 **Internal Gateway**——
由它代持 LLM 金鑰、計量 token、回收事件。
Console 沒有自己的容器，它是 `api` 服務出去的原生靜態檔，打的是跟外部系統同一份 `/v1` API。
圖中標注各層的技術與關鍵參數（佇列、隔離層級、資源上限）。

### 兩個技術核心

**沙盒隔離**（[ADR-0001](specs/adr/ADR-0001-sandbox-isolation.md)，範圍見 [ADR-0005](specs/adr/ADR-0005-mvp-single-host-scope.md)）——
四層 Docker 原生隔離，且**假設每一層都可能被突破**：internal 網段、`cap_drop ALL` + non-root、cgroups 限額、唯讀檔案系統，外加 SDK 攔截層。沙盒一 Run 一盒，絕不重用。
**沒有 gVisor**——`capabilities()` 誠實回報 `l2_kernel_isolation: false`，對應的安全測試標 `skipped` 而非 `passed`。
這個等級適用於**信任的程式碼**，不適用於公開多租戶。

**Celery 與 LangGraph 的邊界**（[ADR-0002](specs/adr/ADR-0002-celery-langgraph-boundary.md)）——
一條判準：*跨機器、需排隊、需獨立擴縮 → Celery；只是同一 Agent 內部的下一步 → LangGraph node。*
一次 Run 就是一個 Celery task，不管圖裡有幾個節點。

### 由 AI agent 團隊協作開發

這個 repo 由 Claude Code 與 Codex 的角色 agent 開發，兩邊讀同一份角色定義。三個角色，不是一套官僚流程：

- **`architect`** 擁有規格、契約與 ADR —— 只在契約要改時才叫
- **`builder`** 寫全部產品程式碼：後端、Console、沙盒
- **`reviewer`** 寫驗收條件、跑測試、稽核程式碼有沒有偏離規格

流程是**刻意分級**的：小改動直接做，不派 subagent、不寫交接單。
只有會改 [`api-contract.md`](specs/api-contract.md)、需要新 ADR、或動到沙盒與憑證邊界的工作，
才走完整的 architect → builder → reviewer。

把這些串起來的是**契約優先**——前後端各自對齊 [`api-contract.md`](specs/api-contract.md)，不用互相等待——
加上[任務看板](coordination/BOARD.md)，以及大功能才寫的交接單。

規則寫在 [`AGENTS.md`](AGENTS.md)，刻意保持很短。
Codex 的角色、skills 與操作範例見 [`docs/codex.md`](docs/codex.md)。

### 本機啟動與測試

本機與正式是同一套 compose，沒有第二種部署方式。

```bash
docker compose up -d --build                     # postgres · redis · registry · api · worker
docker build -t zentra/agent-base:1.0 -f docker/agent-base.Dockerfile .
docker compose exec api alembic upgrade head

curl -s localhost:8000/healthz                   # {"status":"ok"}
open http://localhost:8000/console/agents        # Console
```

compose **不會**幫你做兩件事：

- **Base image。** `zentra/agent-base:1.0` 是你自己的 agent image 的 `FROM`。
  它不屬於任何 compose 服務（沒有東西會「跑」它），所以 compose 沒有地方可以 build 它。
  改過 `packages/sdk/` 就重 build 一次；已經發布的 image 不受影響，它們的內容早就凍住了。
- **migration。** 啟動時不會自動跑。

**每個 Run 用的是 agent 自己的 image**，依該版本註冊的 digest 拉下來，這條路徑上沒有共用的 runtime 容器。
唯一的例外是 ZEN-000 留下的內建 fixture `builtin-echo`，它仍然跑 `zentra/agent-runtime:dev`
（`SANDBOX_IMAGE`），也就是下面那支冒煙測試跑的對象。**只有要跑那支腳本才需要 build 它**：

```bash
docker build -t zentra/agent-runtime:dev -f docker/agent-runtime.Dockerfile .
```

退休這個 fixture 已[記在 BOARD](coordination/BOARD.md)：它是冒煙測試與 9 個測試檔的承重結構，
是一個獨立任務，不是刪幾行就好。

Registry 發布在**主機 port 6000**，不是 registry 慣用的 5000：macOS 的 AirPlay Receiver
在 `*:5000` 聽，會蓋掉它。容器內仍是 5000，換的只有主機端
（[ADR-0009](specs/adr/ADR-0009-internal-registry-image-upload.md) R3）。
確認要看 header 而不是「連得上」——別的服務也會回應：

```bash
curl -sI http://127.0.0.1:6000/v2/ | grep -i docker-distribution-api-version
```

維運細節見 [`docs/operations.md`](docs/operations.md)。

兩個網段（含 internal 的 `zentra-sandbox-net`）都寫在 `compose.yaml` 裡，不用手動 `docker network create`。
`.env` 是選用的。Console 的靜態檔包在 api 映像裡，所以**改前端要 `docker compose up -d --build api`** 才看得到。

### 發布一個 Agent

完整流程，每一行都在這套 stack 上實際跑過。

#### 1. 寫 `agent.py`

```python
from typing import TypedDict
from langgraph.graph import END, START, StateGraph
from zentra_sdk import agent

class State(TypedDict, total=False):
    question: str
    answer: str

@agent(
    name="my-agent",
    version="1.0.0",
    entrypoints=[{
        "name": "default",
        "description": "這個 entrypoint 做什麼",
        "input_schema":  {"type": "object", "required": ["question"],
                          "properties": {"question": {"type": "string"}}},
        "output_schema": {"type": "object", "required": ["answer"],
                          "properties": {"answer": {"type": "string"}}},
    }],
)
def build(ctx) -> StateGraph:
    graph = StateGraph(State)

    async def node(state: State) -> dict:
        return {"answer": f"收到：{state['question']}"}

    graph.add_node("node", node)
    graph.add_edge(START, "node")
    graph.add_edge("node", END)
    return graph
```

`entrypoints` 是**唯一**的 schema 來源：REST 的輸入驗證、MCP 的 `tools/list`、per-agent 的 OpenAPI
全部從它生成（P3）。改了它就要發新版本。

模組必須能以 `agent` 這個名字被 import ——
檔名可以不同，模組名不行（[ADR-0003](specs/adr/ADR-0003-agent-packaging-dual-interface.md)）。

#### 2. 寫 Dockerfile

```dockerfile
FROM zentra/agent-base:1.0
COPY agent.py /opt/zentra/app/agent.py
```

Base image 已經備好 SDK、相依套件、`io.zentra.image-contract` label 與 uid 65534。
想自己從頭寫也可以，[`examples/demo-echo/Dockerfile`](examples/demo-echo/Dockerfile)
把契約要求的每一項都攤開寫著 —— 但相依套件就得自己顧。

#### 3. build 並 push

```bash
docker build -t localhost:6000/my-agent:1.0.0 .
docker push  localhost:6000/my-agent:1.0.0
```

push 輸出的最後一行帶著你要的 digest：

```
1.0.0: digest: sha256:58b16bb2… size: 2407
```

那是 **registry manifest digest**。`docker build` 印的那串 sha256 是本機 image ID，
**是不同的識別，不能互相代替**。忘了抄就用：

```bash
docker inspect --format='{{index .RepoDigests 0}}' localhost:6000/my-agent:1.0.0
```

**tag 建議用 semver。** 平台不強制，但這讓每個註冊過的 manifest 都留著一個 tag ——
理由是 [`docs/operations.md`](docs/operations.md) 裡那個垃圾回收的陷阱。

外部 Registry（GHCR、Docker Hub、企業自架）用法完全一樣。平台不在乎是哪一個，只要拉得到那個 digest。

#### 4. 建立 Agent 並註冊版本

在 Console `http://localhost:8000/console/agents`，或走 API：

```bash
AID=$(curl -s -X POST http://localhost:8000/v1/agents \
  -H 'Content-Type: application/json' \
  -d '{"name":"my-agent","description":"..."}' | jq -r .agent_id)

curl -s -X POST "http://localhost:8000/v1/agents/$AID/versions" \
  -H 'Content-Type: application/json' \
  -d "{\"semver\":\"1.0.0\",\"image_ref\":\"localhost:6000/my-agent@sha256:58b16bb2…\"}"
```

回應是 `BUILDING`。平台在背景依 digest 拉 image、inspect，並在**一個一次性沙盒裡**跑
`zentra-agent validate` 抽出 entrypoint schema —— 使用者程式碼永遠不會被平台 import（P1）。輪詢到終態：

```bash
curl -s "http://localhost:8000/v1/agents/$AID/versions/1.0.0" | jq .status
```

`PUBLISHED` 就能跑。`FAILED` 會把驗證容器的完整 traceback 放進 `error.message` ——
**那是你唯一的除錯線索**，要讀它，不要用猜的。

只收 digest：用 tag 會被 `VALIDATION_ERROR` 擋下；同一個 semver 重上回 `409`。
已發布的版本不可變（P5），要改就發新 semver。

#### 5. 執行

```bash
# REST 同步，上限 60 秒
curl -s -X POST "http://localhost:8000/v1/agents/$AID/runs:invoke" \
  -H 'Content-Type: application/json' \
  -d '{"entrypoint":"default","input":{"question":"哈囉"}}'

# 非同步 + 事件流
RID=$(curl -s -X POST "http://localhost:8000/v1/agents/$AID/runs" \
  -H 'Content-Type: application/json' \
  -d '{"entrypoint":"default","input":{"question":"哈囉"}}' | jq -r .run_id)
curl -sN "http://localhost:8000/v1/runs/$RID/events"

# MCP —— 同一個 agent、同一份 schema，都從同一份 entrypoints 生成
curl -s -X POST "http://localhost:8000/mcp/agents/$AID" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"name":"default","arguments":{"question":"哈囉"}}}'
```

事件流是 `QUEUED → SCHEDULING → RUNNING → node.start → node.end → SUCCEEDED`。
每個 Run 有自己的沙盒容器，結束即銷毀，絕不重用。

#### 三個會寫錯的地方

三個都會變成 `BUILD_FAILED`，traceback 在 `error.message`：

| 寫法 | 結果 |
|---|---|
| `def build()` | 被 arity 檢查擋下，訊息直接說出契約要的簽章。`build` 要收 context。 |
| 回傳 `.compile()` 過的 graph | 平台要自己 compile 才能注入事件 hook。你先 compile 了，Run 觀測頁就沒有 `node.start` / `node.end` 可看。 |
| 自己寫 Dockerfile 但漏裝 `jsonschema` | 啟動自檢會說少了什麼。用 base image 就不會發生。 |

#### 沙盒裡刻意沒有的東西

不要在 agent 裡找這些，**沒有它們正是重點**（P4）：

- 任何 API key、LLM 金鑰、registry 憑證
- PostgreSQL 與 Redis
- 對外網路

沙盒掛在一個沒有對外路由的網段上，唯一的對端是平台的 Internal Gateway，由它代持憑證。
呼叫 LLM 要走 `ctx.llm`，而**那個還沒實作**（ZEN-002）——
所以現在的 agent 只能做不需要外部資源的事：運算、資料轉換、graph 流程控制。

#### 發新版本

build、push、註冊一個新的 semver。舊版本照常運作，這正是不可變換來的東西。
同一個 semver 重上是 `409`，而 registry 停用了刪除，所以已發布的版本不會從腳下被抽走。

驗收腳本：一次貫穿加上隔離探測，預期 `通過 27 ／ 失敗 0 ／ 跳過 2`。
那 2 個 skip 是 gVisor 核心隔離與容器逃逸，本架構不提供，因此**永遠是 skipped 而不是 passed**（ADR-0005）。

```bash
bash scripts/verify-zen000.sh
```

測試套件：主機的 Python 沒有應用相依，所以走 `uv`，或在 compose 網段內用 `api` 映像跑。

```bash
uv sync --extra dev && uv run pytest tests/zen006a -q

docker run --rm --network zentra-net -v "$PWD:/srv" -w /srv \
  -e PYTHONPATH=/srv \
  -e ZENTRA_TEST_BASE_URL=http://api:8000 \
  -e ZENTRA_TEST_DB_URL=postgresql+asyncpg://zentra:zentra@postgres:5432/zentra \
  zentra-api:latest \
  sh -c "pip install -q pytest pytest-asyncio anyio && pytest tests/zen006a -q"
```

| 套件 | 範圍 | 前置條件 |
|---|---|---|
| [`tests/zen000`](tests/zen000) | Run 生命週期、沙盒 driver、Internal Gateway、worker 復原、冷啟量測 | `ZENTRA_TEST_BASE_URL`、`ZENTRA_TEST_DB_URL`；driver 測試要 `docker` CLI |
| [`tests/zen006a`](tests/zen006a) | Console 原始碼護欄、實際路由、headless Chrome E2E | 護欄不需要任何服務；路由要 `ZENTRA_TEST_BASE_URL`；E2E 要 `node` 與 Chrome |
| [`tests/zen008`](tests/zen008) | OCI 註冊、MCP 門面、沙盒安全、Console E2E | `:5010` 的 registry、`docker` CLI、`node` 與 Chrome |

**碰資料庫的測試一律在 compose 網段內跑。** 主機若有自己的 Postgres 會佔住 `127.0.0.1:5432`
遮蔽容器那顆，從主機跑就會連到錯的資料庫——那些 `ZENTRA_TEST_DB_URL` 的 skip 就是在擋這件事。

只稽核原始碼的測試不需要服務也不需要相依：

```bash
python3 -m pytest tests/zen006a/test_console_source_contract.py -q
```

它若在「契約版本」那條斷言上紅，是**護欄正常運作**：`api-contract.md` 版本動了，而 Console 還沒對新版重新核對過。
請先修 Console（或確認真的沒變）再更新那個版本字串，**不要只把字串改掉**。

### 從哪裡開始讀

1. [`specs/SPEC.md`](specs/SPEC.md) — 這是什麼、五條核心立場
2. [`docs/architecture/detailed-architecture.md`](docs/architecture/detailed-architecture.md) — 11 張架構圖
3. [`specs/adr/`](specs/adr/) — 為什麼這樣做，而不是別的做法（**先讀 [ADR-0005](specs/adr/ADR-0005-mvp-single-host-scope.md)**，它說明砍了什麼）
4. [`specs/features/ZEN-000/spec.md`](specs/features/ZEN-000/spec.md) — 第一個任務

---

## License

_License not yet chosen._
