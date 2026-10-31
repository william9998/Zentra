# 效能測試（Locust）

對本機跑起來的 Zentra（`make up`）做壓力測試。腳本是 [`locustfile.py`](locustfile.py)，預設值在 [`locust.conf`](locust.conf)。

## 第一次：建 venv

```bash
cd test/performance
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

`.venv/` 已經在 `.gitignore` 裡，不會被提交。

## 跑

**有網頁介面（建議）**：開 http://localhost:8089 ，自己調人數、看即時圖表。

```bash
cd test/performance
.venv/bin/locust
```

**不開網頁、跑固定時間**（適合比較改動前後）：

```bash
.venv/bin/locust --headless -u 20 -r 5 -t 2m --csv results/run
```

`-u` 同時幾個使用者 · `-r` 每秒加幾個 · `-t` 跑多久 · `--csv` 把結果存成 CSV。

**只壓某一種**：在指令最後加類別名稱。

```bash
.venv/bin/locust --headless -u 50 -r 10 -t 1m ApiReadUser   # 只壓 API 讀取
.venv/bin/locust --headless -u 5  -r 1  -t 2m InvokeUser    # 只壓完整 Run
```

## 三種使用者

| 類別 | 權重 | 打什麼 | 量的是什麼 |
|---|---|---|---|
| `ApiReadUser` | 3 | `/healthz`、`GET /v1/agents`、`GET /v1/runs` | app 和 Postgres 的讀取，不開沙盒 |
| `InvokeUser` | 1 | `POST /v1/agents/{id}/runs:invoke` | 一次完整 Run：派工 → 開沙盒 → 回結果 |
| `McpUser` | 1 | `/mcp` 的 `tools/list`、`tools/call` | MCP 接口；`tools/call` 一樣是完整 Run |

權重 3:1:1 的意思是：開 10 個使用者時，大約 6 個在讀 API、2 個在建 Run、2 個在打 MCP。

## 設定（環境變數）

| 變數 | 預設 | 說明 |
|---|---|---|
| `ZENTRA_AGENT_ID` | `builtin-echo` 的 id | 要壓哪個 Agent |
| `ZENTRA_ENTRYPOINT` | `default` | 呼叫哪個入口 |
| `ZENTRA_INPUT` | `{"question": "locust"}` | 送進去的 `input`（JSON 字串） |
| `ZENTRA_MCP_KEY` | 自動發一把 | 沒給的話，開始時發一把只勾這個 Agent 的 key，結束時撤銷 |
| `ZENTRA_MCP_TOOL` | `{agent 名稱}__{entrypoint}` | 通常不用設 |

換主機：`.venv/bin/locust --host http://其他主機:8000`

## 要知道的限制

- **一次性沙盒同時最多 10 個**（`SANDBOX_MAX_CONCURRENT`，見 `specs/dispatcher-resources.md`）。
  超過的 Run 直接 `FAILED` + `QUOTA_EXCEEDED`。所以 `InvokeUser` / `McpUser` 人數拉高，
  失敗率上升是**預期中的上限**，不是 Locust 的問題。
- `builtin-echo` 不打 LLM，量到的是**平台本身的開銷**。要看真實 Agent 的表現，用 `ZENTRA_AGENT_ID` 指定。
- 同步呼叫最多等 60 秒，超過回 `SANDBOX_TIMEOUT`（Run 本身會繼續跑）。
- 規格裡的目標：API 額外延遲 P95 < 100ms（`specs/dispatcher-resources.md`）。

## 跑完收尾

壓測會在平台上留下大量 Run，跑完清掉：

```bash
make clean-test-data
```
