# Agent：怎麼寫、怎麼發布、怎麼執行

一個 Agent = `agent.py` + 兩行 Dockerfile。平台負責 HTTP、認證、事件、API 文件、MCP。

## 最小範例（`examples/my-agent/` 是起點）

```python
from typing import TypedDict
from langgraph.graph import END, START, StateGraph
from zentra_sdk import agent


class State(TypedDict, total=False):
    name: str
    greeting: str


@agent(
    name="hello-agent",
    version="1.0.0",
    entrypoints=[{
        "name": "default",
        "description": "跟指定的人打招呼",
        "input_schema": {"type": "object", "required": ["name"],
                         "properties": {"name": {"type": "string"}}},
        "output_schema": {"type": "object", "required": ["greeting"],
                          "properties": {"greeting": {"type": "string"}}},
    }],
)
def build(ctx) -> StateGraph:
    graph = StateGraph(State)

    async def greet(state: State) -> dict:
        return {"greeting": f"你好，{state['name']}！"}

    graph.add_node("greet", greet)
    graph.add_edge(START, "greet")
    graph.add_edge("greet", END)
    return graph          # 不要 compile
```

```dockerfile
FROM zentra/agent-base:1.0
COPY agent.py /opt/zentra/app/agent.py
```

```bash
docker build -t zentra/agent-base:1.0 -f docker/agent-base.Dockerfile .   # 第一次，在 repo 根目錄
docker build -t localhost:6000/hello-agent:1.0.0 <agent 目錄>
docker push localhost:6000/hello-agent:1.0.0                               # push 就是註冊
```

需要額外套件：

```dockerfile
FROM zentra/agent-base:1.0
USER root
RUN pip install --no-cache-dir pandas==2.2.0
USER 65534:65534
COPY agent.py /opt/zentra/app/agent.py
```

base image 已含 Python 3.12、`langgraph`、`langchain-openai`、`httpx`、`jsonschema`、`zentra_sdk`。
Dockerfile 不要宣告 `VOLUME`（會產生可寫匿名 volume，繞過唯讀檔案系統）。

## 規則（違反會在驗證時被擋）

- 檔案在 `/opt/zentra/app/agent.py`，有 `@agent(...)` 裝飾的 `build(ctx)`，恰好一個參數。
- `build` 回傳**未 compile** 的 `StateGraph`（只支援 LangGraph）。
- `entrypoints` 非空、`name` 不重複，每項**恰好** `name`、`description`、`input_schema`、`output_schema`；兩個 schema 是 JSON Schema 2020-12 且最外層 `type: "object"`。
- 平台用的名稱與版號來自 push 的 repository 與 tag，不是 `@agent` 裡寫的。
- 所有 entrypoint 跑同一張 graph，graph 不知道是哪個入口叫的。
- `description` 是 MCP 對端 LLM 選 tool 的依據，寫清楚。

**輸出 = 最終 state 整份。** `output_schema` 只驗證不過濾，`required` 缺了 Run 失敗。中間產物別放 state。

**`build()` 在驗證時會被呼叫一次**，那時 `ctx.config = {}`、`ctx.secret_names = []`、沒有 run token。所以 `build()` 裡不要 `ctx.config["x"]`（用 `.get`）、不要呼叫 `ctx.llm()` / `ctx.http` / 連網，全部放進節點。

## `ctx`

| 用途 | 用法 | 說明 |
|---|---|---|
| LLM | `await ctx.llm().ainvoke("…")`；`ctx.llm("gpt-4o", temperature=0)` | 平台代打計費，不填金鑰。預設 model 是 Console 設的 |
| 設定 | `ctx.config.get("MAX_ROUNDS", 3)` | 明文，改了下一筆 Run 生效 |
| 機密名稱 | `"X" in ctx.secret_names` | 只有名字，沒有值 |
| 外部 HTTP | `ctx.http.get(url)` | 同步阻塞，寫在 `def` 節點。需先加白名單。上游 4xx/5xx 不拋例外，自己看 `resp.status`；被平台擋拋 `EgressBlocked` |
| 寄信 | `ctx.send_email(to=…, subject=…, text=…)` | 需先設寄件者與收件人名單 |
| log | `ctx.logger.info("msg", key=value)` | 進事件流；`print` 只進容器 log |
| 自訂事件 | `ctx.emit("name", {...})` | |
| run id | `ctx.run_id` | |

沒有 `ctx.secrets`、`ctx.tools`、`ctx.knowledge`。需要認證的外部 API：把 key 存成機密，在白名單規則設 `auth_secret_name` + `auth_header`。

## 容器限制

| 項目 | 限制 |
|---|---|
| 記憶體 | 2 GB、無 swap，超過整個容器被殺（同容器的 Run 一起掛） |
| CPU | 2 core |
| Run 時間 | 預設 / 上限 900 秒 |
| 行程數 | 256 |
| 檔案系統 | 唯讀，只有 `/workspace` 可寫（512 MB tmpfs，noexec） |
| 網路 | 沒有對外路由，只連得到 `app` |
| 身分 | 非 root `65534`，無 capability，不開 port |
| env | 精確集合，沒有任何金鑰 |

一個版本一個常駐容器，最多同時 `SANDBOX_RUNS_PER_CONTAINER`（50）筆 Run 共用：不要用全域變數存單筆 Run 資料、不要在 `async def` 裡做阻塞呼叫。

## 發布與版本

`docker push localhost:6000/<agent 名>:<semver>` = 註冊。repository 是 Agent 名（只能一層，不存在就自動建立），tag 是版號；`latest` 等不符合的 tag 會被略過。也可以用外部 registry 的 digest 呼叫 `POST /v1/agents/{id}/versions`。內部 registry 只綁 `127.0.0.1:6000`，只能本機 push。

驗證（worker 的 `q.build`）：

1. **PULL** 拉精確 digest
2. **INSPECT** 檢查 label `io.zentra.image-contract=zentra.image/v1`、platform、RepoDigests
3. **VALIDATE** 開一次性容器跑 `zentra-agent validate`：import、檢查 `build` 簽章、以空設定呼叫 `build(ctx)`、抽出 entrypoints

通過 → `PUBLISHED`；失敗 → `FAILED`，`error.details.stage` 是上面三者之一。修好後同版號再 push。

**同版號再 push = 取代**：新的驗證通過才切換（舊的變 `SUPERSEDED`，digest 不改寫），失敗則舊的繼續服務。已建立的 Run 照舊 image 跑完。取代後 worker 會停舊容器，沒有別的版本在用該 digest 時刪掉 image 與 registry manifest（blob 要另外 `registry garbage-collect`）。

**刪 Agent**（`DELETE /v1/agents/{id}`）是硬刪：版本、Run、事件、LLM / 出口稽核、憑證、key、設定、機密、規則、MCP 勾選全部刪除，之後 worker 清容器與 image。

## 執行流程

```
POST /runs → app 驗 input、寫 runs(QUEUED)、送 Celery q.run → 202
worker：QUEUED → SCHEDULING；找該版本 READY 且有空位的常駐容器（inflight+1），
        commit 後 LPUSH 工作到 Redis agent_work:{agent}:{version}
        沒有容器 → 冷啟動並等待；起不來 → 改用一次性沙盒
容器長輪詢 /internal/agents/{id}/work 取到工作 → SCHEDULING → RUNNING、發 run token
容器跑 graph，事件 / LLM / HTTP 經 Gateway；跑完 POST /result
worker finalize → SUCCEEDED / FAILED、撤銷 token、inflight-1
```

- 只有 worker 決定 Run 終態；每次轉狀態與 `run.status` 事件同一個交易寫入。
- 全滿時 Run 留在佇列排隊（`SCHEDULING`），排隊時間不計入逾時；超過 `RUN_QUEUE_TIMEOUT_SECONDS` → `SANDBOX_TIMEOUT`。
- 60 秒排程：收逾時的 Run、補派卡在 `QUEUED` 超過 30 秒的 Run、回收閒置超過 `CONTAINER_IDLE_TIMEOUT_SECONDS` 的容器、存活檢查（死掉自動重啟，10 分鐘內第 4 次放棄 → 新 Run 回 `AGENT_NOT_RUNNING`，Console 手動重啟可恢復）。

## 常見錯誤

| 症狀 | 原因 |
|---|---|
| `build must accept exactly one positional argument` | `def build():` |
| `must return an uncompiled StateGraph` | 呼叫了 `.compile()` |
| `each entrypoint must contain name, description, input_schema, output_schema` | 欄位多或少 |
| `entrypoint input/output schemas must describe objects` | schema 最外層不是 object |
| 驗證時 `KeyError` / `ctx.llm() 只能在 Run 的沙盒裡呼叫` | 在 `build()` 裡讀 config 或呼叫 ctx |
| Run `EGRESS_BLOCKED` | 沒加白名單或不符規則，看 `details.reason` |
| output 驗證錯 | 最終 state 缺 `output_schema` 必填欄位 |
| 連外網 timeout | 容器沒有對外網路，用 `ctx.http` |

## 範例

| 目錄 | 示範 |
|---|---|
| `examples/my-agent/` | 起點：多節點、條件分支、`ctx.logger` / `ctx.emit` |
| `examples/llm-agent/` | `ctx.llm()` |
| `examples/stock-agent/` | 多輪搜尋迴圈 |
| `examples/stock-suite/` | `ctx.http`、備援來源、額外套件、`deploy.sh` 一次部署五個 Agent |
| `examples/probe-agent/` | 壓測用的測試 Agent |
