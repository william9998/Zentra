# MCP 系統設計

> 每個有 PUBLISHED 版本的 Agent 自動有一個 MCP server，不用另外設定（P3）。
> 實作在 `apps/api/mcp.py`，掛在同一個 FastAPI app 上。

## 端點

```
http://<host>/mcp/agents/{agent_id}        Streamable HTTP（MCP 標準協定）
```

| MCP 方法 | 行為 |
|---|---|
| `tools/list` | 該 Agent **最新 PUBLISHED 版本**的每個 entrypoint 一個 tool |
| `tools/call` | 建一筆 Run。預設同步等終態（上限 60 秒）；參數帶 `_async: true` 則立刻回 `{run_id, status: "QUEUED", agent_version}` |

`resources/*`、`prompts/*` 不實作。

## 與 REST 同源（P3 的落點）

| | REST | MCP |
|---|---|---|
| input schema | 版本的 `entrypoints[].input_schema` | **同一份**直接當 tool 的 `inputSchema`，不重新生成 |
| output schema | `entrypoints[].output_schema` | 同一份當 `outputSchema` |
| 建 Run | `services.runs.create_run` | **同一個函式**，只差 `trigger = MCP` |
| 派工 | `dispatch_run` → Celery `q.run` | 同一條 |
| API key | `deps.optional_agent_api_key` | `_resolve_key`，規則一字不差（見 [agent-registry.md](agent-registry.md) §下游 API key） |
| 同步上限 | `runs:invoke` 60 秒 | 60 秒 |

兩個門面共用同一份 schema 與同一條執行路徑，所以沒有第二份可以漂移的實作。改其中一邊時另一邊必須一起看。

## 細節

- **版本選擇**：MCP 永遠用最新的 PUBLISHED（依 semver 取最大），**不能指定版本**。同版號被取代後，下一次請求就是新的 image。
- **認證**：`Authorization: Bearer zk_live_…`。沒帶放行（`api_key_id = NULL`），帶了無效 → `401`，別的 Agent 的 → `403`；
  `ZENTRA_REQUIRE_AGENT_API_KEY=true` 時沒帶也 `401`。錯誤在建 server 之前就以統一錯誤格式回 HTTP。
- **`_async`**：保留參數，底線開頭是為了不跟使用者 entrypoint 的欄位撞名；建 Run 前會先從參數裡移除。
- **結果**：`SUCCEEDED` → `structured_content` 是 Run 的 `output`；其他終態 → `ToolError("<error.code>: <message> (run_id=…)")`，客戶端看到 `isError`。
  同步等超過 60 秒 → `ToolError("SANDBOX_TIMEOUT: …")`，**Run 本身繼續跑**，可以用 `run_id` 去 REST 查。
- **沒有 PUBLISHED 版本** → `409 AGENT_NOT_PUBLISHED`；Agent 不存在 → `404`。

## 為什麼每個請求建一個 FastMCP app

tool 清單每個 Agent、每個版本都不同，而 FastMCP 的 Streamable HTTP app 需要自己的 lifespan 才能初始化 session manager。
所以每個請求：解析 `agent_id` → 讀最新版本 → 建 server → 在它自己的 lifespan 裡處理完就收（`stateless_http=True`）。

代價是每次多一次物件建構；換到的是**沒有跨請求共用的可變狀態**，版本發布或取代後不需要快取失效。
要改成快取，請連同「版本發布／取代後怎麼失效」一起設計，不要只加一個 dict。

`/mcp/agents` 是一個原生 ASGI dispatcher（Starlette 的 Mount 不支援路徑參數），自己解析下一段當 `agent_id`。

## 不做

- `resources/*`（對應知識庫，不在範圍內）
- 指定版本的 MCP 端點
- MCP 專屬的認證機制（與 REST 共用下游 API key）
