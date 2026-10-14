# stock-agent —— 帶 retry 迴圈、會上網搜尋的股市分析 Agent

問任何股票或大盤問題，它判斷你問的是個股還是大盤、上網找資料、
**自己檢查找到的東西夠不夠**，不夠就換個問法再找，最後給看多/看空比例與買賣建議。

```
classify ─▶ research ─▶ assess ─┬─(不足且還有次數)─▶ research（帶著「缺什麼」重搜）
                                └─(夠了 或 用完次數)──▶ advise ─▶ END
```

**回頭的那一條就是 retry 迴圈**，也是用 graph 而不是一串函式呼叫的理由。

## 跑一次

```bash
cd examples/stock-agent
docker build -t localhost:6000/stock-agent:1.0.0 .
docker push  localhost:6000/stock-agent:1.0.0
docker inspect --format='{{index .RepoDigests 0}}' localhost:6000/stock-agent:1.0.0
```

註冊之後在 Agent 詳情頁設定 LLM 憑證（它需要一把能上網搜尋的金鑰），然後：

```json
{"question": "台積電最近怎麼樣，現在可以買嗎？"}
```

```json
{
  "subject_type": "STOCK",
  "subject": "TSMC 台積電",
  "summary": "月營收年增 25%，3nm 滿載，外資上調目標價；地緣政治為主要風險。",
  "bullish_percent": 70,
  "bearish_percent": 30,
  "recommendation": "BUY",
  "rationale": "基本面成長明確，風險屬外部且已部分反映。",
  "sources": ["https://…", "https://…"],
  "rounds": 2,
  "data_quality": "GOOD",
  "disclaimer": "…"
}
```

大盤問題（`{"question": "美股大盤現在能買嗎"}`）走同一條路，`subject_type` 是 `MARKET`。

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
⚠️ **這支貼著 60 秒上限跑**：每一輪都是帶網路搜尋的 LLM 呼叫，實測一次約 50 秒，`assess` 判定資料不足而多搜一輪就會超過，回 `SANDBOX_TIMEOUT`。**建議用下面的非同步**，同步只適合確認接線。

```bash
curl -s -X POST "$B/v1/agents/$AID/runs:invoke" \
  -H 'Content-Type: application/json' \
  -d '{"entrypoint": "default", "input": {"question": "台積電最近怎麼樣，現在可以買嗎？"}}'
```

回 `200`，是完整的 Run 物件。你要的東西在 `output`：

```json
{"run_id": "…", "status": "SUCCEEDED", "trigger": "API",
 "output": {"subject_type": "STOCK", "recommendation": "…", "data_quality": "…", …},
 "error": null, "usage": {"llm_calls": 4, "input_tokens": …, "output_tokens": …, "cost_usd": "…"}}
```

> `output` 是 graph 的**最終 state 整份**，所以你送進去的 input 欄位也會出現在裡面。
> `usage` 是平台從 Gateway 記錄算的，不是 agent 自己報的；`cost_usd` 要設定 `LLM_PRICES` 才會非零。

**2. 非同步呼叫** `POST /v1/agents/{id}/runs` —— 立刻回 `202` 與 `run_id`，不受 60 秒限制。

```bash
RID=$(curl -s -X POST "$B/v1/agents/$AID/runs" \
  -H 'Content-Type: application/json' \
  -d '{"entrypoint": "default", "input": {"question": "台積電最近怎麼樣，現在可以買嗎？"}}' \
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

**4. 錯誤長這樣** —— 例如`question` 短於 `minLength: 2`：

```bash
curl -s -X POST "$B/v1/agents/$AID/runs:invoke" \
  -H 'Content-Type: application/json' \
  -d '{"entrypoint": "default", "input": {"question": "a"}}'
```

```json
{"error": {"code": "VALIDATION_ERROR", "message": "input does not match entrypoint schema",
           "run_id": null, "retryable": false,
           "details": {"field": "input.question", "message": "'a' is too short"}}}
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
  -d '{"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "default", "arguments": {"question": "台積電最近怎麼樣，現在可以買嗎？"}}}'
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
  -d '{"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "default", "arguments": {"question": "台積電最近怎麼樣，現在可以買嗎？", "_async": true}}}'
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
  -H 'Content-Type: application/json' -d '{"entrypoint": "default", "input": {"question": "台積電最近怎麼樣，現在可以買嗎？"}}'
```

| 情況 | 結果 |
|---|---|
| 沒帶 key | 放行，用量記在「未歸戶」（MVP 預設；`ZENTRA_REQUIRE_AGENT_API_KEY=true` 才會擋） |
| 帶了有效的 key | 放行，用量記在這把 key 名下 |
| 帶了無效或已撤銷的 key | `401 UNAUTHENTICATED` |
| 帶了**別的 Agent 的** key | `403 PERMISSION_DENIED` |

### 用 Postman 測

這個目錄有現成的 collection：[`stock-agent.postman_collection.json`](./stock-agent.postman_collection.json)。

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

## 網路搜尋怎麼運作

**不是沙盒上網** —— 沙盒沒有對外路由（P4）。是**模型在供應商那一側搜**：

```
沙盒 ──▶ Internal Gateway ──▶ 供應商（它去搜網路）
         換上真金鑰、計量
```

OpenAI 有兩種形狀，能用哪一種取決於你的 model，所以 agent **依序試**：

| 形狀 | 走哪條 API | 特性 |
|---|---|---|
| `web_search` tool | `/responses` | 模型自己決定何時搜，較靈活 |
| `web_search_options` | `/chat/completions` | 只有 `*-search` 系列模型吃 |

平台的 Gateway 兩條都代理（白名單，見 `llm.UPSTREAM_PATHS`）。
兩種都失敗就不帶搜尋硬問一次 —— **不讓 Run 死**，而是讓 `assess` 判定資料不足，
最後由 `data_quality` 誠實回報 `INSUFFICIENT`。

> **關於 model 名稱**：平台把 `model` 字串原樣轉給供應商，不做任何白名單。
> 所以能不能用某個型號，取決於你的金鑰對應的帳號有沒有那個模型 ——
> 平台這邊不需要改任何東西。

## retry 迴圈的三個設計重點

**1. 一定要有上限。** `MAX_RESEARCH_ROUNDS = 3`。沙盒有 15 分鐘 wall-clock 硬上限
（SPEC §10），而每一輪都是一次帶網路搜尋的 LLM 呼叫 —— 很慢也很貴。
沒有上限的條件邊界就是一個會燒錢的無窮迴圈。

**2. 判斷交給模型，不是寫死規則。** `assess` 節點問模型「這些資料夠不夠回答使用者的問題」，
不夠時要它說出**缺什麼**，下一輪把那句話帶進 prompt。寫死「至少要 3 個來源」
那種規則擋不住「找到 5 篇但都是別家公司的新聞」。

**3. 用完次數要誠實。** 到上限仍不滿意時不會失敗，而是用手上的資料作答，
並把 `data_quality` 標成 `PARTIAL`，`rounds` 等於上限。看到這兩個訊號就知道結論的底氣不足。

## 輸出裡的誠實訊號

| 欄位 | 看什麼 |
|---|---|
| `rounds` | 等於 3（上限）表示最後一輪仍不滿意 |
| `data_quality` | `INSUFFICIENT` 表示資料撐不起結論 |
| `recommendation` | `HOLD` 表示**資料不足以給方向**，不是「持平看好」 |
| `sources` | 空陣列表示沒真的搜到東西 |

## ⚠️ 這不是投資建議

輸出固定帶 `disclaimer`。模型看得到的只有它搜到的那些公開資訊，
不知道你的持倉、成本與風險承受度，也可能引用過時或錯誤的來源。

把它當成「把公開資訊整理成固定格式」的工具，不是判斷依據。

## 這支示範了什麼

| | |
|---|---|
| **條件邊界回頭** | `add_conditional_edges` 指回上游節點，這是 LangGraph 的核心能力 |
| **有界的迴圈** | 計數器 + 上限檢查放在 route 函式裡 |
| **多層退化** | Responses API → chat/completions → 不帶搜尋，逐層退而不崩 |
| **`ctx.emit` 追蹤進度** | Run 觀測頁看得到 `research_round` / `assessed` 每一輪的判斷 |
| **State 要宣告完整** | ⚠️ 沒宣告在 `State` 裡的 key **LangGraph 會安靜丟掉**，最後在輸出驗證才爆。這支寫的時候就踩過 |
