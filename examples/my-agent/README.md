# my-agent —— 從這裡開始寫你自己的 Agent

一個**真的會動**的文字分析 Agent：給它一段文字，回傳字數、句數、
平均長度與關鍵詞。複製這個目錄，改 `agent.py` 就是你的了。

```
examples/my-agent/
  agent.py      你的 Agent。改這個。
  Dockerfile    兩行，不用動。
```

## 跑一次

**build context 是這個目錄**，所以要先 `cd` 進來：

```bash
cd examples/my-agent
docker build -t localhost:6000/my-agent:1.0.0 .
docker push  localhost:6000/my-agent:1.0.0
```

接著印出可以直接註冊的完整 reference：

```bash
docker inspect --format='{{index .RepoDigests 0}}' localhost:6000/my-agent:1.0.0
# → localhost:6000/my-agent@sha256:40e064517f42…
```

> **不要貼 `docker push` 的輸出。** 它印的是 `1.0.0: digest: sha256:… size: …`，
> 只有 digest，少了前面的 repository —— 貼進去會被擋下來。
> 也不要用 `docker build` 印的那串 sha256，那是本機 image ID，是另一個識別。

註冊與執行見[根目錄 README 的 Ship an agent](../../README.md#ship-an-agent)。

試試看：

```bash
curl -s -X POST "http://localhost:8000/v1/agents/$AID/runs:invoke" \
  -H 'Content-Type: application/json' \
  -d '{"entrypoint":"default","input":{"text":"Zentra runs every agent inside an isolated sandbox. The sandbox has no route to the internet.","top_n":3}}'
```

```json
{
  "word_count": 19, "unique_words": 17, "sentences": 2,
  "avg_word_length": 4.63, "avg_sentence_length": 9.5,
  "keywords": ["sandbox", "zentra", "runs"],
  "verdict": "19 個字、2 句，主題看起來是：sandbox、zentra、runs"
}
```

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
  -d '{"entrypoint": "default", "input": {"text": "Zentra runs every agent inside an isolated sandbox. The sandbox has no route to the internet.", "top_n": 3}}'
```

回 `200`，是完整的 Run 物件。你要的東西在 `output`：

```json
{"run_id": "…", "status": "SUCCEEDED", "trigger": "API",
 "output": {"word_count": 16, "sentences": 2, "keywords": ["sandbox", "zentra", "runs"], …},
 "error": null, "usage": {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": "0"}}
```

> `output` 是 graph 的**最終 state 整份**，所以你送進去的 input 欄位也會出現在裡面。
> `usage` 是平台從 Gateway 記錄算的，不是 agent 自己報的；`cost_usd` 要設定 `LLM_PRICES` 才會非零。

**2. 非同步呼叫** `POST /v1/agents/{id}/runs` —— 立刻回 `202` 與 `run_id`，不受 60 秒限制。

```bash
RID=$(curl -s -X POST "$B/v1/agents/$AID/runs" \
  -H 'Content-Type: application/json' \
  -d '{"entrypoint": "default", "input": {"text": "Zentra runs every agent inside an isolated sandbox. The sandbox has no route to the internet.", "top_n": 3}}' \
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

**4. 錯誤長這樣** —— 例如`top_n` 超過上限 20：

```bash
curl -s -X POST "$B/v1/agents/$AID/runs:invoke" \
  -H 'Content-Type: application/json' \
  -d '{"entrypoint": "default", "input": {"text": "hello", "top_n": 99}}'
```

```json
{"error": {"code": "VALIDATION_ERROR", "message": "input does not match entrypoint schema",
           "run_id": null, "retryable": false,
           "details": {"field": "input.top_n", "message": "99 is greater than the maximum of 20"}}}
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
  -d '{"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "default", "arguments": {"text": "Zentra runs every agent inside an isolated sandbox. The sandbox has no route to the internet.", "top_n": 3}}}'
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
  -d '{"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "default", "arguments": {"text": "Zentra runs every agent inside an isolated sandbox. The sandbox has no route to the internet.", "top_n": 3, "_async": true}}}'
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
  -H 'Content-Type: application/json' -d '{"entrypoint": "default", "input": {"text": "Zentra runs every agent inside an isolated sandbox. The sandbox has no route to the internet.", "top_n": 3}}'
```

| 情況 | 結果 |
|---|---|
| 沒帶 key | 放行，用量記在「未歸戶」（MVP 預設；`ZENTRA_REQUIRE_AGENT_API_KEY=true` 才會擋） |
| 帶了有效的 key | 放行，用量記在這把 key 名下 |
| 帶了無效或已撤銷的 key | `401 UNAUTHENTICATED` |
| 帶了**別的 Agent 的** key | `403 PERMISSION_DENIED` |

### 用 Postman 測

這個目錄有現成的 collection：[`my-agent.postman_collection.json`](./my-agent.postman_collection.json)。

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

## 這支示範了什麼

| | 為什麼重要 |
|---|---|
| **多節點 + 條件分支** | 文字太短就跳過關鍵詞抽取，走另一條路。這才是用 graph 而不是用一個函式的理由，Run 觀測頁也會如實顯示走了哪幾個節點 |
| **`ctx.logger` / `ctx.emit`** | 寫出去的東西會變成 Run 事件流裡的 `log` 與 `custom` 事件。**不要用 `print`** —— 那只進容器日誌，Console 上看不到 |
| **中間產物不放 state** | 平台把最終 state 整個當成 output 回傳，`output_schema` 只驗證不過濾。把切好的詞陣列放進 state，使用者就會看到幾十筆雜訊 |
| **回傳未 compile 的 graph** | 平台要自己 compile 才能注入事件 hook。你先 `.compile()` 了，Run 觀測頁就沒有 `node.start` / `node.end` 可看 |

## 改成你自己的

1. 改 `@agent(...)` 的 `name` 與 `entrypoints`
   —— `entrypoints` 是**唯一**的 schema 來源，REST 驗證、MCP `tools/list`、
   per-agent OpenAPI 全部從它生成
2. 改節點裡的邏輯
3. 發新版本時 `semver` 要換 —— 同一個 semver 重上會 `409`（版本不可變）

## 三個會讓你卡住的地方

都會在驗證階段變成 `BUILD_FAILED`，完整 traceback 在 `error.message`：

| 寫法 | 結果 |
|---|---|
| `def build()` | 被擋下，訊息會直接告訴你要 `def build(ctx: ZentraContext)` |
| 回傳 `.compile()` 過的 graph | 事件 hook 注入不進去 |
| 自己寫 Dockerfile 但漏裝 `jsonschema` | 啟動自檢會說少了什麼。用 base image 就不會發生 |

## 沙盒裡沒有的東西

- 任何 API key、LLM 金鑰
- PostgreSQL、Redis
- 對外網路

這是刻意的（P4）。沙盒掛在沒有對外路由的網段上，唯一對端是平台的 Internal Gateway。
呼叫 LLM 要走 `ctx.llm()`，由平台代持金鑰 —— 範例見 [`../llm-agent/`](../llm-agent/)。
這支刻意不用它：運算、資料轉換、流程控制這類工作不需要任何外部資源。
