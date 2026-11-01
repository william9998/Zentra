# 效能測試（Locust）

對本機跑起來的 Zentra（`make up`）做壓力測試。腳本是 [`locustfile.py`](locustfile.py)，預設值在 [`locust.conf`](locust.conf)。

壓的是**你正在用的那套服務**，不是另外起一套：Run 會寫進同一個資料庫，跑完記得收尾（見最後一節）。

## 第一次：建 venv、準備測試 Agent

```bash
cd test/performance
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cd ../..
make fixture        # build → push（= 註冊）→ 等 probe-agent 發布
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
.venv/bin/locust --headless -u 20 -r 5 -t 2m --csv results/run --html results/report.html
```

`-u` 同時幾個使用者 · `-r` 每秒加幾個 · `-t` 跑多久 · `--csv` / `--html` 把結果存下來（不加就只印在終端機）。

**只壓某一種**：在指令最後加類別名稱。

```bash
.venv/bin/locust --headless -u 50 -r 10 -t 1m InvokeUser    # 只壓 REST 呼叫 Agent
.venv/bin/locust --headless -u 50 -r 10 -t 1m McpUser       # 只壓 MCP
```

## 三種使用者

| 類別 | 權重 | 打什麼 | 量的是什麼 |
|---|---|---|---|
| `ApiReadUser` | 1 | `/healthz`、`GET /v1/agents`、`GET /v1/runs` | app 和 Postgres 的讀取，不碰容器 |
| `InvokeUser` | 3 | `POST /v1/agents/{id}/runs:invoke` | 一次完整 Run：派工 → 常駐容器 → 回結果 |
| `McpUser` | 3 | `/mcp` 的 `tools/list`、`tools/call` | MCP 接口；`tools/call` 一樣是完整 Run |

權重 1:3:3 的意思是：開 70 個使用者時，大約 10 個在讀 API、30 個在用 REST 呼叫 Agent、30 個在打 MCP。

## 打的是哪個 Agent

預設是測試 Agent **`probe-agent`**（[`examples/probe-agent`](../../examples/probe-agent)）。它和使用者寫的 Agent 走一模一樣的路：
`FROM zentra/agent-base` + SDK、push 註冊、驗證、發布、跑在**常駐容器**裡。

- 它不打 LLM，所以不花錢、結果固定。
- 每筆 Run 預設等 **1 秒**（`ZENTRA_WORK_MS`），模擬「等 LLM 回應」—— 真實 Agent 的時間大多花在這裡。
  設成 `0` 量的是平台本身的開銷；設成 `5000` 模擬比較慢的 Agent。

## 設定（環境變數）

| 變數 | 預設 | 說明 |
|---|---|---|
| `ZENTRA_AGENT_NAME` | `probe-agent` | 要壓哪個 Agent（用名稱找） |
| `ZENTRA_AGENT_ID` | （空） | 直接給 id，就不用名稱找 |
| `ZENTRA_WORK_MS` | `1000` | probe-agent 每筆 Run 模擬等待幾毫秒 |
| `ZENTRA_INPUT` | `{"question": "locust", "work_ms": <ZENTRA_WORK_MS>}` | 送進去的 `input`（JSON 字串）。**換別的 Agent 時一定要改**，要符合它的 `input_schema` |
| `ZENTRA_ENTRYPOINT` | `default` | 呼叫哪個入口 |
| `ZENTRA_MCP_KEY` | 自動發一把 | 沒給的話，開始時發一把只勾這個 Agent 的 key，結束時撤銷 |
| `ZENTRA_MCP_TOOL` | `{agent 名稱}__{entrypoint}` | 通常不用設 |

例：`ZENTRA_WORK_MS=3000 .venv/bin/locust`　換主機：`.venv/bin/locust --host http://其他主機:8000`

## 看結果時要知道的

- **一個版本只有一個常駐容器，同時最多跑 50 筆**（`SANDBOX_RUNS_PER_CONTAINER`，見 `specs/dispatcher-resources.md`）。
  超過的在 Redis 排隊、等容器空出位子，所以人數拉高時你會看到**回應時間變長，而不是馬上失敗**。
  估算：每筆等 1 秒、同時 50 筆，大約每秒 50 筆就是上限，再多就開始排隊。
- **同步呼叫最多等 60 秒**。排隊加執行超過 60 秒會回 `SANDBOX_TIMEOUT`（Run 本身會繼續跑完）。
- **第一筆會慢幾秒**：容器是第一筆 Run 進來時才啟動的（冷啟動）。閒置 30 分鐘沒人用，平台會把容器收掉，下一次又是冷啟動。
- 規格裡的目標：API 額外延遲 P95 < 100ms（`specs/dispatcher-resources.md`）。

## 跑完收尾

壓測會在平台上留下大量 Run，跑完清掉（probe-agent 本身會留著）：

```bash
make clean-test-data
```
