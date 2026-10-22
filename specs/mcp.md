# MCP 接口

> 平台只有**一個** MCP 接口：`/mcp`。
> Agent 不各自開 MCP 端點，而是被「勾選」進某把 MCP key，變成這把 key 看得到的 **tools**。
> 外部 AI 平台只要接一次，就能看到清單、讓自己的 LLM 挑要呼叫哪個。

程式在哪：協定 `apps/api/mcp.py` · key 與勾選 `apps/api/services/mcp_keys.py` · Console 用的 REST `apps/api/routers/mcp_keys.py`。

## 先看整體怎麼運作

```
你在 Console 的 MCP 頁                    外部 AI 平台（例如 Dify）
  1. 發一把 key：zmcp_live_xxxx             4. 連 http://host/mcp，帶 Bearer key
  2. 勾選 stock-agent、echo-agent           5. tools/list → 看到兩個 Agent 的 tools
  3. 把 key 交給對方 ───────────────────▶   6. 它的 LLM 挑 stock-agent__default
                                            7. tools/call → 平台建 Run、執行、回結果
```

重點：
- **一個網址**。新增 Agent 不用叫對方改設定，你在 Console 勾選就會出現在清單。
- **每把 key 各自勾選**。給 A 平台勾 stock-agent，給 B 平台勾 echo-agent，互相看不到對方的。
- **沒帶 key 連不上**。因為沒 key 就不知道該列哪些 tools。

## 端點

```
http://<host>/mcp        （/mcp 和 /mcp/ 都可以）
```

協定：Streamable HTTP（MCP 標準）。**無狀態**：不用先 `initialize`，每個請求獨立。

請求標頭：

```
Authorization: Bearer zmcp_live_…
Content-Type: application/json
Accept: application/json, text/event-stream
```

| MCP 方法 | 做什麼 |
|---|---|
| `tools/list` | 列出這把 key 勾選的每個 Agent、其**最新已發布版本**的每個 entrypoint，一個 entrypoint = 一個 tool |
| `tools/call` | 建一筆 Run 並等結果（最多 60 秒）；參數帶 `_async: true` 則不等，立刻回 `run_id` |

不實作：`resources/*`、`prompts/*`。

## 範例

### 1. 列出 tools

```bash
curl -s -X POST http://localhost:8000/mcp \
  -H "authorization: Bearer $KEY" \
  -H 'content-type: application/json' \
  -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

回應（精簡過）：

```json
{"jsonrpc":"2.0","id":1,"result":{"tools":[{
  "name": "stock-agent__default",
  "description": "[stock-agent] 問任何股票或大盤問題，回新聞面整理、看多/看空比例與買賣建議",
  "inputSchema":  {"type":"object","properties":{"question":{"type":"string"}},"required":["question"]},
  "outputSchema": {"type":"object","properties":{"...":"..."}}
}]}}
```

怎麼看：
- `name` = `{Agent 名稱}__{entrypoint 名稱}`。同一個 Agent 有多個 entrypoint 就會有多個 tool。
- `description` = `[Agent 名稱] ` + entrypoint 的說明。**對端的 LLM 就是靠這句話決定要不要呼叫它**，所以 Agent 的說明要寫清楚「能幹嘛」。
- `inputSchema` 就是這個 entrypoint 的輸入格式，LLM 照它填參數。

### 2. 呼叫 tool（同步，等結果）

```bash
curl -s -X POST http://localhost:8000/mcp "${HEADERS[@]}" -d '{
  "jsonrpc":"2.0","id":2,"method":"tools/call",
  "params":{"name":"stock-agent__default","arguments":{"question":"台積電最近怎麼樣"}}}'
```

成功時 `structuredContent` 就是 Run 的 `output`。

### 3. 呼叫 tool（非同步，不等）

在 `arguments` 加 `"_async": true`：

```json
{"name":"stock-agent__default","arguments":{"question":"台積電","_async":true}}
```

立刻回：

```json
{"run_id":"01a161b7-8bfb-…","status":"QUEUED","agent_version":"1.0.0"}
```

之後用 REST 查：`GET /v1/runs/{run_id}`。適合很慢的 Agent。
`_async` 底線開頭，是為了不跟 Agent 自己的欄位撞名；它在建 Run 之前就會被拿掉，不會傳給 Agent。

### 4. 失敗長什麼樣

MCP 的錯誤**不是 HTTP 4xx**。HTTP 還是 `200`，差在 `"isError": true`：

```json
{"result":{"content":[{"type":"text","text":"Unknown tool: 'nope__x'"}],"isError":true}}
```

| 情況 | 你會看到 |
|---|---|
| 沒帶 key、key 無效或已撤銷 | **HTTP `401`**（在進入 MCP 之前就擋掉，格式同 [api.md](api.md) 的錯誤格式） |
| 呼叫不在清單裡的 tool（沒勾選、沒已發布版本、Agent 已刪除） | `isError`，`Unknown tool`。刻意不透露那個 Agent 存不存在 |
| Run 失敗 | `isError`，文字是 `<error.code>: <message> (run_id=…)` |
| 同步等超過 60 秒 | `isError`，`SANDBOX_TIMEOUT: …`。**Run 本身會繼續跑**，用 `run_id` 去 REST 查 |

## Tool 是怎麼生出來的

| 欄位 | 來源 |
|---|---|
| `name` | `{agent.name}__{entrypoint.name}`。名稱裡不在 `[A-Za-z0-9_-]` 的字元換成 `_`（Agent 名稱允許 `.`，但有些 MCP 客戶端不收） |
| `description` | `[{agent.name}] ` + entrypoint 的 `description` |
| `inputSchema` / `outputSchema` | 版本裡 `entrypoints[].input_schema` / `output_schema`，**原封不動，不重新生成** |

幾個規則：
- **只用最新的已發布版本**（依 semver 取最大），不能指定版本。同版號被取代後，下一次請求就是新 image。
- **沒有已發布版本、或已刪除的 Agent，不會出現在清單**。勾選會保留，發布之後自動出現。
- **名稱撞到**（例如 `a.b` 和 `a_b` 換字後都變 `a_b`）：後面那個不列出，並在 log 記一筆 warning。別取這種名字。

### 和 REST 的關係（P3）

兩個入口共用同一份資料和同一條執行路徑，所以不會各說各話：

| | REST | MCP |
|---|---|---|
| 輸入格式 | 版本的 `entrypoints[].input_schema` | **同一份**，當 tool 的 `inputSchema` |
| 輸出格式 | `entrypoints[].output_schema` | **同一份**，當 `outputSchema` |
| 建 Run | `services.runs.create_run` | **同一個函式**，`trigger = MCP`，多寫 `runs.mcp_key_id` |
| 派工 | `dispatch_run` → Celery `q.run` | 同一條 |
| 同步等待上限 | `runs:invoke` 60 秒 | 60 秒 |

改其中一邊時，另一邊要一起檢查。

## MCP key

- 格式 `zmcp_live_<隨機字串>`。**明文只在發 key 的那次回應出現一次**，之後沒有任何方式能再看到；遺失就重發。
- 庫裡只存 SHA-256 雜湊，加上前綴（例如 `zmcp_live_a1b2`，列表裡認得出是哪一把）。
- 和 Agent 的下游 API key（`zk_live_…`）是**兩套不同的東西**：

  | | Agent API key（`zk_live_`） | MCP key（`zmcp_live_`） |
  |---|---|---|
  | 綁什麼 | 單一 Agent | 一組 Agent（多對多） |
  | 用在哪 | REST 建 Run | `/mcp` |
  | 沒帶會怎樣 | 預設放行（只是記帳用） | 一律 `401` |

  `/mcp` 只認 `zmcp_live_…`，拿 `zk_live_…` 來是 `401`。
- **勾選立刻生效**，下一次 `tools/list` 就是新的。
- **撤銷是軟的**：立刻失效，但那一列留著（它建過的 Run 還掛在上面），已撤銷的 key 不能再改勾選（`409`）。
- 管理 key 的 REST 端點在 [api.md](api.md) §MCP key，Console 的 MCP 頁就是用它們。

## 用量歸戶

MCP 建的 Run：`api_key_id = NULL`、`mcp_key_id = <這把 key>`。
Agent 詳情的「依 key 用量」只認 `api_key_id`，所以 MCP 的用量會歸在 `key_id: null` 那一列。要按 MCP key 看用量需要另外做。

## 實作細節：為什麼每個請求都重建一個 FastMCP

tool 清單**每把 key 不同、每個版本也不同**，而 FastMCP 的 HTTP app 需要自己的 lifespan 才能初始化。
所以每個請求做這些事：驗 key → 讀勾選的 Agent 和最新版本 → 建 server → 在它自己的 lifespan 裡處理完就收（`stateless_http=True`）。

- 代價：每次多一次物件建構和幾個查詢。
- 換到的：**沒有跨請求共用的狀態**，勾選改了、版本發布了都不用處理快取失效。

要改成快取，請連「勾選變更、版本發布或取代後怎麼讓快取失效」一起設計，不要只加一個 dict。

路由：`/mcp` 是直接掛的 ASGI endpoint（`/mcp` 和 `/mcp/` 都接），不用 Starlette 的 Mount——Mount 會把 `/mcp` 用 307 轉到 `/mcp/`，很多 MCP 客戶端轉址時不會重送 POST 內容。

## 不做

- `resources/*`（對應知識庫，不在範圍內）
- 每個 Agent 各自的 MCP 端點（已移除，`/mcp/agents/{id}` 是 `404`）
- 指定版本的 tool
- 把 MCP 用量歸到 Agent API key 的報表
