# demo-echo —— 最小的 Zentra Agent image

一個能跑完整條 ZEN-008 流程的示範：**註冊 → 驗證 → 發布 → 執行**。
把這個目錄複製一份改成你自己的 Agent。

> 這份是照著實際跑通的流程寫的。以下每一步都在單機 compose 上驗證過。

---

## 先理解一件事：平台不收你的 image 檔案

Zentra **不接受上傳 `.tar`、不接受 `docker save`、不接受 tag**。
你自己把 image push 到 registry，平台只收一個 **digest**：

```
docker build → docker push → 在 Console 填 semver + repo@sha256:... → 平台自己 pull
```

原因是 P5（版本不可變）：`version_id` 就是 image digest 的前 12 碼。
image 不可變、不重傳，發布後也不會因為有人動了 tag 而換掉內容。
執行時平台只按 digest 拉，**永遠不解析 `latest`**。

---

## 跑一次

### 1. registry 已經在 compose 裡了

`docker compose up -d` 會帶起內部 Registry，位址 `localhost:6000`（ZEN-009 / ADR-0009）。
不用自己起，也不用處理憑證 —— Docker 對 `localhost` 開頭的 registry 預設允許不走 TLS。

外部 Registry（Docker Hub、GHCR、企業 registry）**一樣可以用**，在 API 上是同一件事。

> 主機端是 **6000** 不是 5000，因為 macOS 的 AirPlay Receiver 佔著 `*:5000`。
> 容器內仍是 5000，換的只有主機端。

### 2. build 並 push

**build context 是 repo 根目錄**（Dockerfile 要複製 `packages/sdk`）：

```bash
docker build -f examples/demo-echo/Dockerfile -t localhost:6000/demo-echo:1.0.0 .
docker push localhost:6000/demo-echo:1.0.0
```

push 的輸出會給你 digest，長這樣：

```
1.0.0: digest: sha256:ce3605d5b3fd... size: 2200
```

**把那個 `sha256:...` 抄下來**，下一步要用。

### 3. 建立 Agent 並註冊版本

Console：`http://localhost:8000/console/agents` → 建立 Agent → 註冊新版本，
填 semver 與 `localhost:6000/demo-echo@sha256:...`。

或用 API：

```bash
AID=$(curl -s -X POST http://localhost:8000/v1/agents \
  -H 'Content-Type: application/json' \
  -d '{"name":"demo-echo","description":"示範"}' \
  | python3 -c 'import sys, json; print(json.load(sys.stdin)["agent_id"])')

curl -s -X POST "http://localhost:8000/v1/agents/$AID/versions" \
  -H 'Content-Type: application/json' \
  -d "{\"semver\":\"1.0.0\",\"image_ref\":\"localhost:6000/demo-echo@sha256:...\"}"
```

回應是 `BUILDING`。平台會非同步 pull image、在**一個隔離的一次性沙盒**裡跑
`zentra-agent validate`，抽出 entrypoints schema，然後轉成 `PUBLISHED`。

查狀態：

```bash
curl -s "http://localhost:8000/v1/agents/$AID/versions/1.0.0"
```

成功會看到：

```json
{
  "status": "PUBLISHED",
  "artifact": {
    "runtime_contract": "zentra.image/v1",
    "platform": "linux/arm64/v8",
    "size_bytes": 219437944
  },
  "entrypoints": [{ "name": "default", ... }]
}
```

失敗會是 `FAILED`，而且 `error.message` **帶著驗證容器裡的完整 traceback** ——
那是你唯一的除錯線索，好好看。

### 4. 執行

三個門面都可以，跑的是同一條路：

```bash
# REST（同步，上限 60 秒）
curl -s -X POST "http://localhost:8000/v1/agents/$AID/runs:invoke" \
  -H 'Content-Type: application/json' \
  -d '{"entrypoint":"default","input":{"question":"哈囉"}}'

# MCP
curl -s -X POST "http://localhost:8000/mcp/agents/$AID" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"name":"default","arguments":{"question":"哈囉"}}}'
```

Console 則是在 Agent 詳情頁選 entrypoint 發起，然後在 Run 觀測頁看事件流。

---

<!-- testing:start -->
## 測試：REST 與 MCP

同一個 Agent 平台自動開了兩個門面（P3），**input schema 是同一份**（`agent.py` 裡的
`entrypoints`）。下面的請求形狀、回應格式與錯誤行為都對單機 compose 實測過
（驗證用的是 my-agent；四個範例走的是同一套端點，只有 `input` 不同）。

先設兩個變數，後面每段都會用到：

```bash
B=http://localhost:8000
AID=<agent_id>   # Console → Agent 詳情頁的 AGENT ID，或建立 Agent 時回傳的 agent_id
```

### REST

**1. 同步呼叫** `POST /v1/agents/{id}/runs:invoke` —— 等到終態才回，上限 **60 秒**。
純運算，幾秒內回來。

```bash
curl -s -X POST "$B/v1/agents/$AID/runs:invoke" \
  -H 'Content-Type: application/json' \
  -d '{"entrypoint": "default", "input": {"question": "哈囉"}}'
```

回 `200`，是完整的 Run 物件。你要的東西在 `output`：

```json
{"run_id": "…", "status": "SUCCEEDED", "trigger": "API",
 "output": {"question": "哈囉", "echo": "哈囉"},
 "error": null, "usage": {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": "0"}}
```

> `output` 是 graph 的**最終 state 整份**，所以你送進去的 input 欄位也會出現在裡面。
> `usage` 是平台從 Gateway 記錄算的，不是 agent 自己報的；`cost_usd` 要設定 `LLM_PRICES` 才會非零。

**2. 非同步呼叫** `POST /v1/agents/{id}/runs` —— 立刻回 `202` 與 `run_id`，不受 60 秒限制。

```bash
RID=$(curl -s -X POST "$B/v1/agents/$AID/runs" \
  -H 'Content-Type: application/json' \
  -d '{"entrypoint": "default", "input": {"question": "哈囉"}}' \
  | python3 -c 'import sys, json; print(json.load(sys.stdin)["run_id"])')

curl -s "$B/v1/runs/$RID" | python3 -m json.tool             # 輪詢，直到 status 是終態
curl -s -N -H 'Accept: text/event-stream' "$B/v1/runs/$RID/events"   # 或看即時事件流
```

`status` 會走 `QUEUED → SCHEDULING → RUNNING → SUCCEEDED | FAILED`。
事件流是 SSE，每個節點的 `node.start` / `node.end`、`ctx.logger` 的 `log`、`ctx.emit` 的 `custom`
都在裡面，最後一個是 `run.status`。斷線重連帶 `Last-Event-ID` 就會從那之後續傳。

`input` 以外還可以帶 `"version": "1.0.0"`（指定版本，省略用最新 PUBLISHED）與
`"config": {"timeout_seconds": 300}`（上限 900）。

**3. 看它收什麼** —— 每個 Agent 自動有一份 OpenAPI：

```bash
curl -s "$B/v1/agents/$AID/openapi.json"
```

**4. 錯誤長這樣** —— 例如少了必填的 `question`：

```bash
curl -s -X POST "$B/v1/agents/$AID/runs:invoke" \
  -H 'Content-Type: application/json' \
  -d '{"entrypoint": "default", "input": {}}'
```

```json
{"error": {"code": "VALIDATION_ERROR", "message": "input does not match entrypoint schema",
           "run_id": null, "retryable": false,
           "details": {"field": "input", "message": "'question' is a required property"}}}
```

回 `422`，**不會建立 Run**。全平台只有這一種錯誤格式，`code` 是固定枚舉。

### MCP

端點 `POST $B/mcp/agents/$AID`，Streamable HTTP、JSON-RPC 2.0。兩件事要知道：

- **`Accept` 一定要同時列 `application/json, text/event-stream`**，只寫其中一個會回 `406`。
- 回應是 SSE 格式：一行 `event: message`、一行 `data: {…JSON-RPC…}`。結果在 `data` 那行。

伺服器是**無狀態**的：不用先 `initialize`、沒有 `Mcp-Session-Id`，每個請求獨立。
（MCP client 照協定先送 `initialize` 也沒問題，會正常回 capabilities。）

**1. 列出 tool** —— 每個 entrypoint 一個 tool，`inputSchema` 就是 REST 用的那份：

```bash
curl -s -X POST "$B/mcp/agents/$AID" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

**2. 呼叫** —— `arguments` 就是 REST 的 `input`，同樣上限 60 秒：

```bash
curl -s -X POST "$B/mcp/agents/$AID" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "default", "arguments": {"question": "哈囉"}}}'
```

```
event: message
data: {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"{…output 的 JSON 字串…}"}],
       "structuredContent":{…同一份 output…},"isError":false}}
```

`structuredContent` 是給程式讀的，`content[0].text` 是同一份內容的字串版（給只讀文字的 client）。

**3. 非同步** —— 在 `arguments` 裡多帶保留參數 `"_async": true`，立刻回 `run_id`，
之後用 REST 的 `GET /v1/runs/{run_id}` 或事件流追：

```bash
curl -s -X POST "$B/mcp/agents/$AID" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "default", "arguments": {"question": "哈囉", "_async": true}}}'
# → "structuredContent":{"run_id":"…","status":"QUEUED","agent_version":"1.0.0"}
```

**4. 錯誤** —— MCP 的錯誤**不是** HTTP 4xx，HTTP 一樣是 `200`，差在 `"isError": true`：

```
data: {"jsonrpc":"2.0","id":4,"result":{"content":[{"type":"text",
       "text":"Error calling tool 'default': input does not match entrypoint schema"}],"isError":true}}
```

用 MCP 觸發的 Run，在 Runs 列表的「入口」欄是 `MCP`。

### 帶 API key（用量歸戶）

在 Agent 詳情頁發一把 key（或 `POST /v1/agents/$AID/api-keys`，body `{"name":"postman"}`），
**`zk_live_…` 只會顯示這一次**。兩個門面同一套規則：

```bash
K=zk_live_...
curl -s -X POST "$B/v1/agents/$AID/runs:invoke" -H "Authorization: Bearer $K" \
  -H 'Content-Type: application/json' -d '{"entrypoint": "default", "input": {"question": "哈囉"}}'
```

| 情況 | 結果 |
|---|---|
| 沒帶 key | 放行，用量記在「未歸戶」（MVP 預設；`ZENTRA_REQUIRE_AGENT_API_KEY=true` 才會擋） |
| 帶了有效的 key | 放行，用量記在這把 key 名下 |
| 帶了無效或已撤銷的 key | `401 UNAUTHENTICATED` |
| 帶了**別的 Agent 的** key | `403 PERMISSION_DENIED` |

### 用 Postman 測

這個目錄有現成的 collection：[`demo-echo.postman_collection.json`](./demo-echo.postman_collection.json)。

1. Postman → **Import** → 選這個檔案。
2. 點 collection → **Variables** 分頁，填 `agent_id`（必填）；`base_url` 預設 `http://localhost:8000`；
   `api_key` 留空就是不帶 key，要測歸戶再填。
3. 依序送：

   | 資料夾 | 請求 | 看什麼 |
   |---|---|---|
   | REST | 1 同步呼叫 | `200`、`output` |
   | REST | 2 非同步建立 | `202`；**Tests script 會把 `run_id` 存進 collection 變數** |
   | REST | 3 查 Run | 重送幾次，看 `status` 走到終態 |
   | REST | 4 事件流 (SSE) | Postman 會把每個事件列出來，Run 結束時連線自己關 |
   | REST | 5 OpenAPI | 這個 Agent 的 input schema |
   | REST | 6 驗證錯誤 | `422 VALIDATION_ERROR` |
   | MCP | 1 tools/list | 與 REST 同一份 schema |
   | MCP | 2 tools/call | `structuredContent` 就是 output |
   | MCP | 3 tools/call 非同步 | 也會存 `run_id`，接著可以回 REST 3、4 追 |

幾個 Postman 特有的地方：

- **認證設在 collection 層**（Authorization → Bearer Token = `{{api_key}}`），每個請求繼承。
  `api_key` 空著時送出的是空的 `Bearer`，平台當成沒帶 key。
- **MCP 回應在 Body 裡是 SSE 原文**（`event:` / `data:` 兩行），不是可以摺疊的 JSON。
  結果在 `data:` 那行，複製出來貼到任何 JSON viewer 看。
- Postman 也有原生的 **MCP 請求**（New → MCP，transport 選 HTTP，URL 填
  `{{base_url}}/mcp/agents/{{agent_id}}`），它會自己做 `initialize` 與 `tools/list`，
  直接用表單填 arguments。想看協定原文用 collection，想點點看用原生 MCP 請求。
<!-- testing:end -->

## 三個會讓你卡住的地方

這三個都是我們實際踩過的，契約文件目前沒寫清楚：

### 1. `build()` 要收一個參數

```python
def build(ctx) -> StateGraph:      # ✅
def build() -> StateGraph:         # ❌ TypeError: build() takes 0 positional arguments
```

runtime 呼叫的是 `build(context)`。

### 2. `jsonschema` 必須裝

SDK 的 `image_runtime` 需要它。少了會在 import 階段就死：

```
ModuleNotFoundError: No module named 'jsonschema'
```

### 3. 回傳**未 compile** 的 `StateGraph`

不要自己 `.compile()`。平台要拿未 compile 的 graph 才能注入事件 hook ——
node 的開始/結束事件就是這樣來的。你 compile 過了，Run 觀測頁就沒有事件可看。

---

## 更短的寫法：從 base image 開始

這個目錄的 `Dockerfile` 把契約要求的每一項都攤開寫，是為了讓你看到平台到底要什麼。
實際要寫自己的 Agent 時，用官方 base image 就好 —— 它已經備好 SDK、依賴、label、uid：

```dockerfile
FROM zentra/agent-base:1.0
COPY agent.py /opt/zentra/app/agent.py
```

```bash
docker build -t zentra/agent-base:1.0 -f docker/agent-base.Dockerfile .   # 一次就好
```

## 這個 image 裡有什麼

| 檔案 | 作用 |
|---|---|
| `agent.py` | 你的 Agent。`@agent(...)` 宣告 entrypoints，`build(ctx)` 回傳 `StateGraph` |
| `Dockerfile` | 契約要求的：`io.zentra.image-contract` label、`/opt/zentra/bin/zentra-agent`、uid 65534 |

`@agent` 裝飾器裡的 `entrypoints` 是**唯一的 schema 來源**：
REST 的 `input` 驗證、MCP 的 `tools/list`、per-agent 的 OpenAPI 全部從它生成（P3）。
改了它就要發新版本 —— 同一個 semver 不能重用（P5）。

## 沙盒裡你拿不到什麼

不要在 Agent 程式碼裡找這些，它們**刻意不在**（P4）：

- 任何 API key、LLM 金鑰、registry 憑證
- PostgreSQL / Redis 連線
- 對外網路

沙盒掛在一個沒有對外路由的網段上，唯一的對端是平台的 Internal Gateway。
要呼叫 LLM 請用 `ctx.llm`（ZEN-002 提供），由平台代持金鑰。
