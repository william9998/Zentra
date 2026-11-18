# MCP 接口

平台只有一個 MCP 接口 `/mcp`。Agent 被勾選進某把 MCP key，成為那把 key 看得到的 tools。

程式：協定 `apps/api/mcp.py` · key 與勾選 `apps/api/services/mcp_keys.py` · REST `apps/api/routers/mcp_keys.py`。

## 端點

`http://<host>/mcp`（`/mcp` 與 `/mcp/` 都接，直接掛 ASGI，不用 Mount 以免 307）。Streamable HTTP，無狀態，不需要 `initialize`。

```
Authorization: Bearer zmcp_live_…
Content-Type: application/json
Accept: application/json, text/event-stream
```

| 方法 | 做什麼 |
|---|---|
| `tools/list` | 列出這把 key 勾選的每個 Agent、最新 `PUBLISHED` 版本的每個 entrypoint（一個 entrypoint = 一個 tool） |
| `tools/call` | 建 Run 並等結果（最多 60 秒）；`arguments` 帶 `"_async": true` 則立刻回 `{run_id, status, agent_version}` |

不實作 `resources/*`、`prompts/*`。

```bash
curl -s -X POST http://localhost:8000/mcp \
  -H "authorization: Bearer $KEY" -H 'content-type: application/json' \
  -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

## Tool 怎麼生成

| 欄位 | 來源 |
|---|---|
| `name` | `{agent.name}__{entrypoint.name}`，`[A-Za-z0-9_-]` 以外換成 `_` |
| `description` | `[{agent.name}] ` + entrypoint 的 `description` |
| `inputSchema` / `outputSchema` | 版本的 `entrypoints[].input_schema` / `output_schema`，原封不動 |

- 只用最新 `PUBLISHED` 版本，不能指定版本。
- 沒有已發布版本或已刪除的 Agent 不列出；勾選保留，發布後自動出現。
- 換字後名稱撞到 → 後者不列，log warning。
- 每個請求都重建一個 FastMCP server（驗 key → 讀勾選與版本 → 建 server → 處理完就收），沒有跨請求快取。

## 錯誤

MCP 錯誤是 HTTP `200` + `"isError": true`，只有認證錯是 HTTP `401`。

| 情況 | 結果 |
|---|---|
| 沒帶 key、無效、已撤銷 | HTTP `401`（REST 錯誤格式） |
| 不在清單的 tool | `isError`，`Unknown tool`（不透露 Agent 存不存在） |
| Run 失敗 | `isError`，`<error.code>: <message> (run_id=…)` |
| 版本被放棄 | 同上，`AGENT_NOT_RUNNING` |
| 同步超過 60 秒 | `isError`，`SANDBOX_TIMEOUT`，Run 繼續跑 |

成功時 `structuredContent` = Run 的 `output`。`_async` 在建 Run 前移除，不傳給 Agent。

## 與 REST 共用

REST 和 MCP 共用同一份 entrypoints schema、同一個 `services.runs.create_run`（MCP 時 `trigger = MCP`、寫 `runs.mcp_key_id`）、同一條派工路徑、同樣 60 秒同步上限。改一邊要檢查另一邊。

## MCP key

- 格式 `zmcp_live_…`，明文只在建立時出現一次；DB 只存 SHA-256 與前綴。
- 綁一組 Agent（多對多），勾選立刻生效。軟撤銷，已撤銷的不能改勾選（`409`）。
- `/mcp` 只認 `zmcp_live_…`；`zk_live_…` → `401`。
- MCP 建的 Run：`api_key_id = NULL`、`mcp_key_id = <key>`，所以在 `usage.by_key` 裡歸到 `key_id: null`。
- 管理端點看 [api.md](api.md) §MCP key。
