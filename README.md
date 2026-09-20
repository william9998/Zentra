<div align="center">

# Zentra

**An agent platform where every agent runs in an isolated sandbox and ships with both a REST API and an MCP endpoint.**

[繁體中文](#繁體中文) · [Architecture](docs/architecture/detailed-architecture.md) · [Spec](specs/SPEC.md) · [ADRs](specs/adr/)

</div>

---

Zentra lets you build an AI agent in two ways — assemble one in the console, or upload your own Python code.
Either way, the agent runs inside an isolated sandbox and is automatically published behind **two front doors: a REST API and an MCP endpoint**, generated from a single definition so they can never drift apart.

> **Status: design phase.** The architecture, specifications and agent-team workflow are complete.
> Implementation starts at [`ZEN-000`](specs/features/ZEN-000/spec.md). No application code yet.

## Why this exists

Running untrusted user code is the hard part of any agent platform. Most of the design work here goes into one question: **what stops an uploaded agent from reading another tenant's data or stealing platform credentials?**

The answer is five commitments that the rest of the architecture follows from:

| | Commitment |
|---|---|
| **P1** | User code **never executes inside a platform process**. Celery workers only dispatch and observe — every `exec` happens in a sandbox. |
| **P2** | Agents are **always LangChain + LangGraph**. One framework means checkpointing, streaming, HITL and tracing are written once. |
| **P3** | Every agent gets **both an API and an MCP interface**, generated from the same `entrypoints` definition. |
| **P4** | **No long-lived credential ever enters a sandbox.** Outbound calls go through Tool / LLM / Egress proxies that hold the secrets. |
| **P5** | **Published versions are immutable.** `agent@semver` freezes its manifest, bundle and lockfile. Traffic moves via aliases. |

## Architecture

![Zentra system architecture](docs/architecture/zentra-architecture.svg)

Two node pools inside one Kubernetes cluster. Platform services run on ordinary container runtime; sandboxes run on **dedicated nodes under gVisor**, kept off the same machines by taint/toleration — so a sandbox escape lands the attacker among other sandboxes, not among platform services or credentials.

```
L0  Access        REST Gateway · MCP Gateway · SSE · Console BFF
L1  Control       Agent Service · Registries · Build · Version · Policy · Secrets
L2  Dispatch      Celery — queues, workers, retries, dead letter
L3  Execution     Sandbox Manager → Sandbox (gVisor) running a LangGraph
L4  Knowledge     Ingestion → pgvector + OpenSearch → hybrid search
L5  Data          PostgreSQL · Redis · S3/MinIO · ClickHouse
L6  Observability OpenTelemetry · Langfuse · Prometheus · Audit log
```

**[→ 14 detailed diagrams](docs/architecture/detailed-architecture.md)** covering the request lifecycle, sandbox isolation model, Celery topology, agent runtime, knowledge pipeline, data model and deployment.

### The two hard parts

**Sandbox isolation** ([ADR-0001](specs/adr/ADR-0001-sandbox-isolation.md)) — gVisor plus five defence layers, on the assumption that each one can be breached: network policy, user-space kernel, cgroup limits, read-only filesystem, and an SDK interception layer. Sandboxes are never reused across runs.

**Celery ↔ LangGraph boundary** ([ADR-0002](specs/adr/ADR-0002-celery-langgraph-boundary.md)) — one rule: *crosses machines, needs queueing, or scales independently → Celery; just the next step inside one agent → a LangGraph node.* One run is one Celery task, however many nodes the graph has.

## Repository layout

| Path | What's in it |
|---|---|
| [`specs/`](specs/) | **Single source of truth.** Master spec, API & UI contracts, glossary, ADRs, feature specs |
| [`docs/architecture/`](docs/architecture/) | Mermaid diagrams and design deep-dives |
| [`coordination/`](coordination/) | Task board, handoff notes, spec-drift audits |
| [`.claude/agents/`](.claude/agents/) | Seven role agents — architect, UI, frontend, backend, platform, QA, spec-keeper |
| [`.claude/commands/`](.claude/commands/) | `/align` `/task` `/handoff` `/verify` `/drift` |

## Built by a team of AI agents

This repository is set up so several Claude Code agents can work on it together without stepping on each other. They can't talk directly, so coordination happens through files:

- **One spec** everyone reads before starting ([`specs/SPEC.md`](specs/SPEC.md))
- **Contract-first** — frontend and backend both target [`api-contract.md`](specs/api-contract.md) instead of waiting on each other
- **Handoff notes** and a [task board](coordination/BOARD.md) carry state between roles
- **A spec-keeper agent** audits the code for drift away from the spec

The protocol is written up in [`AGENTS.md`](AGENTS.md).

## Stack

Python 3.12 · FastAPI · Celery · LangChain + LangGraph · PostgreSQL + pgvector · Redis · RabbitMQ · OpenSearch · Kubernetes + gVisor · Next.js · TypeScript · Tailwind

## Getting started

Implementation hasn't begun. To understand the project:

1. [`specs/SPEC.md`](specs/SPEC.md) — what it is and the five commitments
2. [`docs/architecture/detailed-architecture.md`](docs/architecture/detailed-architecture.md) — how it fits together
3. [`specs/adr/`](specs/adr/) — why it's built this way rather than some other way
4. [`specs/features/ZEN-000/spec.md`](specs/features/ZEN-000/spec.md) — the first task

Local development targets Docker via Colima on macOS; production targets Kubernetes with gVisor. The split is documented in [ADR-0004](specs/adr/ADR-0004-sandbox-driver-and-dev-environment.md) and [local dev notes](docs/architecture/07-local-dev-sandbox.md).

---

<a name="繁體中文"></a>

## 繁體中文

**Zentra 是一個 Agent 平台**：使用者可以在 Console 組裝 Agent，或上傳自己的 Python 程式碼。
不論哪一種，Agent 都在**隔離沙盒**中執行，並自動對外開放 **REST API** 與 **MCP** 兩種接口——
兩者由同一份 `entrypoints` 定義生成，因此永遠不會不一致。

> **目前狀態：設計階段。** 架構、規格與 AI agent 團隊協作流程已完成。
> 實作從 [`ZEN-000`](specs/features/ZEN-000/spec.md) 開始，尚無應用程式碼。

### 為什麼這樣設計

執行不可信的使用者程式碼是 Agent 平台最難的部分。整份設計都在回答一個問題：
**要怎麼確保上傳的 Agent 讀不到別的租戶的資料、偷不走平台憑證？**

答案是五條核心立場，其餘架構都是它們的推論：

| | 立場 |
|---|---|
| **P1** | 使用者程式碼**永遠不在平台行程內執行**。Celery worker 只派工與監看，`exec` 一律在沙盒。 |
| **P2** | Agent 框架**統一 LangChain + LangGraph**。收斂成一種，checkpoint、串流、HITL、觀測才只要寫一次。 |
| **P3** | 每個 Agent **天生就有 API + MCP 兩個門面**，由同一份定義生成。 |
| **P4** | **沙盒內拿不到任何長期憑證**。對外一律走 Tool / LLM / Egress Proxy，由 Proxy 代持憑證。 |
| **P5** | **發布的版本不可變**。`agent@semver` 凍結 manifest、bundle 與 lockfile，流量靠 alias 切換。 |

### 架構圖

![Zentra 系統架構](docs/architecture/zentra-architecture.svg)

一個 Kubernetes 叢集、兩個節點池。平台服務跑一般容器 runtime；沙盒跑在 **gVisor 專用節點**上，以 taint/toleration 與平台服務實體分開——即使有人從沙盒逃逸，他落腳的機器上也只有其他沙盒，碰不到平台服務與憑證。

### 兩個技術核心

**沙盒隔離**（[ADR-0001](specs/adr/ADR-0001-sandbox-isolation.md)）——
gVisor 加五層縱深防禦，且**假設每一層都可能被突破**：網路策略、使用者態核心、資源限制、唯讀檔案系統、SDK 攔截層。沙盒一 Run 一盒，絕不重用。

**Celery 與 LangGraph 的邊界**（[ADR-0002](specs/adr/ADR-0002-celery-langgraph-boundary.md)）——
一條判準：*跨機器、需排隊、需獨立擴縮 → Celery；只是同一 Agent 內部的下一步 → LangGraph node。*
一次 Run 就是一個 Celery task，不管圖裡有幾個節點。

### 由 AI agent 團隊協作開發

這個 repo 設計成可以讓多個 Claude Code agent 一起工作而不互相踩踏。
它們無法直接對話，所以協作全部落在檔案上：

- **一份共同規格**，所有人開工前必讀（[`specs/SPEC.md`](specs/SPEC.md)）
- **契約優先**——前後端各自對齊 [`api-contract.md`](specs/api-contract.md)，不用互相等待
- **交接單**與[任務看板](coordination/BOARD.md)串起角色之間的狀態
- **spec-keeper agent** 定期稽核程式碼是否偏離規格

完整協議見 [`AGENTS.md`](AGENTS.md)。

### 從哪裡開始讀

1. [`specs/SPEC.md`](specs/SPEC.md) — 這是什麼、五條核心立場
2. [`docs/architecture/detailed-architecture.md`](docs/architecture/detailed-architecture.md) — 14 張架構圖
3. [`specs/adr/`](specs/adr/) — 為什麼這樣做，而不是別的做法
4. [`specs/features/ZEN-000/spec.md`](specs/features/ZEN-000/spec.md) — 第一個任務

---

## License

_License not yet chosen._
