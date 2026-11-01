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

## 量記憶體：讓每筆 Run 像真的一樣重

光是「等 1 秒」量不出記憶體壓力 —— 一筆只帶幾個字的 Run 幾乎不佔記憶體。
真實 Agent 會佔記憶體的每個來源都有一個旋鈕：

| 真實 Agent | 怎麼模擬 | 記憶體佔在哪裡 |
|---|---|---|
| 使用者貼一大段文字 | `ZENTRA_QUESTION_CHARS=100000` | app（解析、驗證）、排隊時的 Redis、Postgres |
| 文件、對話歷史、工具結果放在 state 裡 | `ZENTRA_CONTEXT_KB=10240` | 常駐容器（等待期間一直留著） |
| LLM 回來的長文字 | `ZENTRA_REPLY_KB=64` | 容器、回應、Postgres 的 output |
| 等 LLM 時連線掛著 | `ZENTRA_MODE=llm` + 假 LLM（下面） | 容器 + app（Gateway 收完整段回覆才轉回容器） |

壓測時另開一個終端機跑 `bash memwatch.sh`，每隔幾秒記一次容器、app、Redis 的記憶體與排隊筆數，存到 `results/mem.csv`。

### 走真的 LLM 連線（假 LLM）

```bash
docker run -d --rm --name zentra-mock-llm --network zentra-net \
  -v "$PWD/mock_llm.py:/mock_llm.py:ro" python:3.12-slim \
  python /mock_llm.py --bind 0.0.0.0 --delay-ms 2000 --reply-kb 32

ZENTRA_MODE=llm ZENTRA_MOCK_LLM_URL=http://zentra-mock-llm:8090/v1 ZENTRA_CONTEXT_KB=1024 .venv/bin/locust

docker stop zentra-mock-llm      # 用完關掉
```

- 假 LLM 要**跑在 Docker 網路裡**，不要跑在主機上經 `host.docker.internal` 連回來：在 Colima 上實測會有一成的呼叫卡在剛好 70 秒。
- 壓測開始時，locustfile 把 probe-agent 的 LLM 憑證指向假 LLM；**等這個 Agent 的 Run 全部跑完才撤掉**。
  提早撤掉的話，排隊中的 Run 會退回平台預設的真金鑰、打到真的供應商。等超過 3 分鐘還沒跑完就不撤，會印出手動刪的指令。
- probe-agent 已經設了自己的 LLM 憑證時，壓測會拒絕開始（API 讀不回原本的金鑰，蓋掉就還原不了）。

### 不要拿這個壓到正式環境

`ZENTRA_CONTEXT_KB` 夠大時（例如 `61440`，50 筆 × 60 MB > 2 GB）**會把常駐容器撐爆**：
跑到一半的 Run 全部失敗，10 分鐘內死 4 次平台就放棄這個版本，之後的 Run 一律 `AGENT_NOT_RUNNING`。
要恢復：Console 的容器頁按「重啟」，或 `POST /v1/agents/{id}/containers/{container_id}/restart`。

## 設定（環境變數）

| 變數 | 預設 | 說明 |
|---|---|---|
| `ZENTRA_AGENT_NAME` | `probe-agent` | 要壓哪個 Agent（用名稱找） |
| `ZENTRA_AGENT_ID` | （空） | 直接給 id，就不用名稱找 |
| `ZENTRA_WORK_MS` | `1000` | probe-agent 每筆 Run 模擬等待幾毫秒 |
| `ZENTRA_QUESTION_CHARS` | `0` | 問題有多少字（`0` = 固定送 `"locust"`） |
| `ZENTRA_CONTEXT_KB` | `0` | 每筆 Run 在 state 裡留多少 KB 的資料 |
| `ZENTRA_REPLY_KB` | `0` | 回答多少 KB（`simulate` 模式） |
| `ZENTRA_MODE` | `simulate` | `llm` = 真的呼叫 `ctx.llm()`，要配 `ZENTRA_MOCK_LLM_URL` |
| `ZENTRA_MOCK_LLM_URL` | （空） | 假 LLM 的位址（從 app 容器看出去的），有給才會改憑證 |
| `ZENTRA_INPUT` | 由上面幾個組出來 | 直接給整個 `input`（JSON 字串），上面的旋鈕就不用了。**換別的 Agent 時一定要改**，要符合它的 `input_schema` |
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
