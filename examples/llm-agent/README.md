# llm-agent —— 會呼叫 LLM 的 Agent

跟 [`../my-agent/`](../my-agent/) 的差別只有一行：**`ctx.llm()`**。
金鑰的事你完全不用管，而那正是重點。

## 金鑰在哪（P4）

```
你的程式碼              Internal Gateway              供應商
──────────              ────────────────              ──────
ctx.llm()           ──▶ 驗 run token
  api_key =             換上真金鑰              ──▶    OpenAI 相容端點
  run token             計量 token
                        寫 llm_calls
                    ◀── 原樣回傳                ◀──
```

沙盒的 env 只有三個變數：`ZENTRA_RUN_TOKEN`、`ZENTRA_GATEWAY_URL`、`ZENTRA_RUN_ID`。
**沒有 `OPENAI_API_KEY`**，而且沙盒網段沒有對外路由 ——
就算你把金鑰硬寫進程式碼也打不出去。

這不是靠紀律，是靠 `worker` 手上根本沒有金鑰：那幾個變數只宣告在 `app` 服務上
（看 `compose.yaml`）。worker 是注入沙盒 env 的那一個，它沒有的東西注入不了。

## 要先設定什麼

`app` 服務要有供應商金鑰。放在 `.env`（只有 `app` 讀得到）：

```bash
ZENTRA_LLM_API_KEY=sk-...
ZENTRA_LLM_BASE_URL=https://api.openai.com/v1     # 預設值，相容端點都可以換
ZENTRA_LLM_DEFAULT_MODEL=gpt-4o-mini              # 預設值
```

**不要**把它加進 `compose.yaml` 裡 `worker` 的 `environment`。那會讓金鑰有機會進沙盒。

### 或者：這個 Agent 用自己的金鑰

不想全部 Agent 共用一把的話，可以幫單一 Agent 註冊專屬憑證（ZEN-012）：

```bash
curl -X PUT "http://localhost:8000/v1/agents/$AID/llm-credential" \
  -H 'Content-Type: application/json' \
  -d '{"api_key":"sk-...","base_url":"https://api.openai.com/v1","default_model":"gpt-4o-mini"}'
```

**憑證綁 Agent 不綁版本**：發新版不用重填，更新則該 Agent 所有版本立刻一起變。
Gateway 的順序是「Agent 自己的 → 平台預設」。

金鑰**寫得進去讀不出來** —— `GET` 只回尾四碼：

```json
{"configured": true, "last_four": "WXYZ", "base_url": "...", "updated_at": "..."}
```

平台要先設定 `ZENTRA_SECRET_KEY`（靜態加密用），否則會拒絕儲存而不是存明文。
與 P5 的互動見 [ADR-0011](../../specs/gateway.md)。

沒設金鑰的話，呼叫會回一個說得清楚的錯：

```json
{"code": "LLM_ERROR",
 "message": "the platform has no LLM credential configured; set ZENTRA_LLM_API_KEY on the app service"}
```

## 跑一次

```bash
cd examples/llm-agent
docker build -t localhost:6000/llm-agent:1.0.0 .
docker push  localhost:6000/llm-agent:1.0.0
```

註冊與執行見[根目錄 README 的 Ship an agent](../../README.md#ship-an-agent)。

```bash
curl -s -X POST "http://localhost:8000/v1/agents/$AID/runs:invoke" \
  -H 'Content-Type: application/json' \
  -d '{"entrypoint":"default","input":{"text":"要摘要的長文字…","style":"一句話"}}'
```

回應會帶上平台算的用量：

```json
{
  "status": "SUCCEEDED",
  "output": {"summary": "…", "used_llm": true, "word_count": 19},
  "usage": {"llm_calls": 1, "input_tokens": 11, "output_tokens": 7, "cost_usd": "0"}
}
```

> `usage` **是平台從 `llm_calls` 表算出來的，不是你的程式碼報的。** 沙盒跑的是使用者
> 程式碼，它報的用量沒有理由被當成帳。`cost_usd` 要設定 `llm_prices` 才會非零 ——
> SPEC §11 把計費出帳列在範圍外，這裡只做計量。

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
會呼叫一次 LLM，通常幾秒到十幾秒。**要先設定 LLM 憑證**（見上面「要先設定什麼」）。`text` 少於 8 個字（以空白切）會走 `too_short` 分支、不叫模型 —— 所以範例用英文；沒有空白的中文整段只算 1 個字。

```bash
curl -s -X POST "$B/v1/agents/$AID/runs:invoke" \
  -H 'Content-Type: application/json' \
  -d '{"entrypoint": "default", "input": {"text": "Zentra runs every agent inside an isolated sandbox with no route to the internet. When the agent calls a model, the request goes through the platform gateway, which adds the real key and records token usage.", "style": "條列"}}'
```

回 `200`，是完整的 Run 物件。你要的東西在 `output`：

```json
{"run_id": "…", "status": "SUCCEEDED", "trigger": "API",
 "output": {"summary": "…", "used_llm": true, "word_count": 36, …},
 "error": null, "usage": {"llm_calls": 1, "input_tokens": …, "output_tokens": …, "cost_usd": "…"}}
```

> `output` 是 graph 的**最終 state 整份**，所以你送進去的 input 欄位也會出現在裡面。
> `usage` 是平台從 Gateway 記錄算的，不是 agent 自己報的；`cost_usd` 要設定 `LLM_PRICES` 才會非零。

**2. 非同步呼叫** `POST /v1/agents/{id}/runs` —— 立刻回 `202` 與 `run_id`，不受 60 秒限制。

```bash
RID=$(curl -s -X POST "$B/v1/agents/$AID/runs" \
  -H 'Content-Type: application/json' \
  -d '{"entrypoint": "default", "input": {"text": "Zentra runs every agent inside an isolated sandbox with no route to the internet. When the agent calls a model, the request goes through the platform gateway, which adds the real key and records token usage.", "style": "條列"}}' \
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

**4. 錯誤長這樣** —— 例如`style` 不在 enum 裡：

```bash
curl -s -X POST "$B/v1/agents/$AID/runs:invoke" \
  -H 'Content-Type: application/json' \
  -d '{"entrypoint": "default", "input": {"text": "hello", "style": "寫成一首詩"}}'
```

```json
{"error": {"code": "VALIDATION_ERROR", "message": "input does not match entrypoint schema",
           "run_id": null, "retryable": false,
           "details": {"field": "input.style", "message": "'寫成一首詩' is not one of ['一句話', '條列', '白話']"}}}
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
  -d '{"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "default", "arguments": {"text": "Zentra runs every agent inside an isolated sandbox with no route to the internet. When the agent calls a model, the request goes through the platform gateway, which adds the real key and records token usage.", "style": "條列"}}}'
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
  -d '{"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "default", "arguments": {"text": "Zentra runs every agent inside an isolated sandbox with no route to the internet. When the agent calls a model, the request goes through the platform gateway, which adds the real key and records token usage.", "style": "條列", "_async": true}}}'
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
  -H 'Content-Type: application/json' -d '{"entrypoint": "default", "input": {"text": "Zentra runs every agent inside an isolated sandbox with no route to the internet. When the agent calls a model, the request goes through the platform gateway, which adds the real key and records token usage.", "style": "條列"}}'
```

| 情況 | 結果 |
|---|---|
| 沒帶 key | 放行，用量記在「未歸戶」（MVP 預設；`ZENTRA_REQUIRE_AGENT_API_KEY=true` 才會擋） |
| 帶了有效的 key | 放行，用量記在這把 key 名下 |
| 帶了無效或已撤銷的 key | `401 UNAUTHENTICATED` |
| 帶了**別的 Agent 的** key | `403 PERMISSION_DENIED` |

### 用 Postman 測

這個目錄有現成的 collection：[`llm-agent.postman_collection.json`](./llm-agent.postman_collection.json)。

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

## 條件分支同時是成本決策

```python
def route(state):
    return "call_llm" if state["word_count"] >= MIN_WORDS_FOR_LLM else "too_short"
```

短文字叫模型摘要是浪費。graph 讓「什麼時候該花錢」變成流程裡看得見的一個節點，
而不是藏在某個 `if` 裡面。

## 能用 OpenAI 的模型，不能用 OpenAI 的 agent 框架

這點最容易誤會：

| | 支援嗎 |
|---|---|
| 用 GPT 系列模型 | ✅ `ctx.llm()` 就是 OpenAI 相容的 |
| 換成其他相容供應商 | ✅ 改 `app` 的 `ZENTRA_LLM_BASE_URL`，你的程式碼一行都不用動 |
| 用 OpenAI Agents SDK 編排 | ❌ P2：框架統一 LangChain + LangGraph |

`build()` 必須回傳未 compile 的 `StateGraph`，runtime 會實際檢查型別。
理由與代價評估見 [ADR-0010](../../specs/overview.md)。
