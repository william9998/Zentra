# stock-suite —— 五個股票分析 Agent（= 五個 MCP tools）

給外部排程（例如 Stock-Insight-Chat）定期分析使用者的庫存與觀察清單用。
五個 Agent 共用 `stockkit/` 這包程式碼與同一份 Dockerfile。

| MCP tool | 做什麼 | 資料來源 | 每次 egress |
|---|---|---|---|
| `stock-news__default` | 三組關鍵字網路搜尋：近 1 月 / 1 季 / 1 年新聞、即將到來的大事、產業與總經 | 模型網路搜尋（Responses API `web_search`） | 0 |
| `stock-chart__default` | 近半年日 K 線圖 PNG（MA5/20/60、量、成本線）+ 持倉損益 | Yahoo chart | 1–2 |
| `stock-technical__default` | MA、RSI、MACD、KD、布林、量比、區間報酬 + 規則訊號 | Yahoo chart | 1–2 |
| `stock-fundamental__default` | 近 8 季損益與三率、月營收、資產負債、現金流、估值、配息 | 台股 FinMind／美股 Yahoo fundamentals | TW 6／US 1 |
| `portfolio-advisor__default` | 整體部位（台幣市值、權重、損益）+ 分析師配置建議 | 內部呼叫上面 1–4，或吃 `precomputed` | 0–8 × 檔數 + 1 |

台股的 `.TW`（上市）/`.TWO`（上櫃）自動判斷，呼叫端只要給數字代碼。

## 輸入

**個股型（1–4）**：`additionalProperties: false`，多一個欄位、少 `use_llm` 都直接失敗。

```json
{"symbol": "2330", "name": "台積電", "market": "TW", "cost": 900, "shares": 1000, "use_llm": true}
```

| 欄位 | 必填 | 說明 |
|---|---|---|
| `symbol` | ✅ | 台股數字代碼（`2330`、`00878`）或美股 ticker（`AAPL`、`BRK.B`）。帶 `.TW` 也會自動去掉 |
| `name` | ✅ | 股票名稱。新聞搜尋的關鍵字靠它 |
| `market` | ✅ | `TW` / `US` |
| `use_llm` | ✅ | `true` 回 `analysis`；`false` 時 `analysis` 為 `null` |
| `cost`、`shares` | 選填 | 觀察清單不填。stock-news **不收**這兩欄 |

**portfolio-advisor**：

```json
{
  "holdings": [{"symbol": "2330", "name": "台積電", "market": "TW", "cost": 900, "shares": 1000}],
  "use_llm": true,
  "investor_profile": {"risk_tolerance": "BALANCED", "cash_twd": 300000},
  "precomputed": [{"symbol": "2330", "market": "TW",
                   "news": {…stock-news 的完整輸出…}, "technical": {…}, "fundamental": {…}, "chart": {…}}]
}
```

- `holdings` 只放**已買庫存**（cost、shares 必填），1–30 檔，同市場同代碼不能重複。
- `precomputed` 選填。有給的部分直接用，**包括它的 `analysis`**，所以先用 `use_llm=true` 呼叫 1–4 再傳進來，分析師拿到的資訊最完整。
  會檢查 `tool` 與 `security.symbol` 對不對得上，對不上就丟棄並重抓（寫 warning）。
  **stock-chart 的輸出請先清掉 `data.image.base64`**：這裡用不到圖，留著輸入會多約 120 KB／檔。
- 沒給的部分由 portfolio-advisor 自己抓（等同 `use_llm=false`）。K 線圖不會自己畫，因為文字 LLM 讀不了圖。

## 輸出（五個都一樣的信封）

```json
{
  "tool": "stock-technical",         // 自報身分，呼叫端可以用它確認拿到的是自己叫的那個
  "schema_version": "1",
  "as_of": "2026-11-07T07:40:00+00:00",
  "security": {"symbol": "2330", "name": "台積電", "market": "TW", "cost": 900, "shares": 1000},
  "data": {…},                       // 原始內容，use_llm 開或關都有
  "analysis": {…} | null,            // use_llm=false 一律 null
  "warnings": ["…"],                 // 部分資料抓不到，Run 仍算成功
  "disclaimer": "…"
}
```

portfolio-advisor 沒有 `security`；`data` 是 `{portfolio, holdings[], egress_budget}`，每檔的
`holdings[].parts.{news,technical,fundamental,chart}.source` 會標明資料來源：
`precomputed`／`fetched`／`derived`／`missing`。
`analysis.allocation[]` 每檔一列（`ADD`/`HOLD`/`TRIM`/`EXIT`、目前與建議權重），現金固定是 `symbol: "CASH"` 那一列。

各 tool 的 `data` / `analysis` 欄位定義寫在 `stockkit/<tool>.py` 的 `DATA_SCHEMA` / `ANALYSIS_SCHEMA`，
也就是 `tools/list` 回的 `outputSchema`。

**`use_llm=true` 但 `analysis` 是 `null`** 表示 LLM 那一步失敗，原因在 `warnings`。資料本身仍可用。

### 什麼時候整筆 Run 會失敗

| 情況 | MCP 回的 |
|---|---|
| 輸入不符 schema（多欄位、少 `use_llm`、market 不是 TW/US） | `isError`：`input does not match entrypoint schema` |
| 查不到行情（代碼或市場錯） | `isError`：`USER_CODE_ERROR: NoMarketData: 查不到 …` |
| stock-news 三組搜尋全失敗（model 不支援網路搜尋） | `isError`：`USER_CODE_ERROR: RuntimeError: 三組新聞搜尋都失敗 …` |
| tool 名稱拼錯 | `isError`：`Unknown tool` |

## 怎麼呼叫（排程用）

新聞約 35–55 秒、portfolio-advisor 在持股多且沒有 precomputed 時可能好幾分鐘，都可能超過 MCP 同步等待的 60 秒上限。
**排程一律用 `_async: true`**，拿 `run_id` 之後輪詢 `GET /v1/runs/{run_id}`，直到 `status` 是
`SUCCEEDED` / `FAILED` / `CANCELLED`。中間狀態還有 `QUEUED`、`SCHEDULING`、`RUNNING`。

```bash
curl -s -X POST $B/mcp -H "authorization: Bearer $ZMCP_KEY" \
  -H 'content-type: application/json' -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"stock-technical__default",
       "arguments":{"symbol":"2330","name":"台積電","market":"TW","use_llm":true,"_async":true}}}'
# → {"run_id":"…","status":"QUEUED","agent_version":"1.0.0"}
curl -s $B/v1/runs/<run_id>     # status 為終態後，output 就是上面的信封
```

實測（2026-11-07，單機 compose，預設 model）：3 檔 × 4 個 tool 同時跑 6 個約 58 秒；接著 portfolio-advisor（全部 precomputed）27 秒。

## 部署

```bash
make up
examples/stock-suite/deploy.sh                 # build → push（= 註冊）→ 等發布 → 設 egress 規則
```

最後印出五個 `名稱<TAB>agent_id`。到 Console 的 MCP 頁發一把 key，**只勾這五個**，交給呼叫端。
重跑是安全的（image 沒變就不重新驗證；規則已存在就跳過）。只改一個：`deploy.sh stock-technical`。

### egress 規則（deploy.sh 會設）

| Agent | host | path_prefix |
|---|---|---|
| stock-chart、stock-technical | `query1.finance.yahoo.com` | `/v8/finance/chart/` |
| stock-fundamental | `api.finmindtrade.com`、`query2.finance.yahoo.com` | `/api/v4/data`、`/ws/fundamentals-timeseries/` |
| portfolio-advisor | 上面三個 | |
| stock-news | 不需要 | |

FinMind 匿名就能用，但有流量限制。要用 token：`FINMIND_TOKEN='Bearer <token>' deploy.sh`。
**值要含 `Bearer ` 前綴**，因為平台會把機密原值直接塞進 `Authorization` header。

### 設定（Console → Agent → 設定，改了不用發版）

| key | 誰用 | 預設 |
|---|---|---|
| `SEARCH_MODEL` | stock-news、portfolio-advisor 的網路搜尋 | Agent 的預設 model |
| `ANALYSIS_MODEL` | 所有 `use_llm=true` 的解讀 | Agent 的預設 model |
| `VISION_MODEL` | stock-chart 看圖 | Agent 的預設 model |
| `EGRESS_BUDGET` | portfolio-advisor 自己抓資料時的 egress 上限 | 90（平台硬上限 100） |
| `MAX_PARALLEL` | portfolio-advisor 同時處理幾檔 | 4 |

## 已知限制

- **不是截圖。** 沙盒沒有網路也沒有瀏覽器，K 線圖是用日線自己畫的（mplfinance），因此沒有看盤網站的指標疊圖。
- **新聞品質取決於 model 的搜尋。** 條目只收有日期、搜得到的；`company_news` 的分桶是程式依日期算的，不信模型的分類。
  搜尋結果偶爾會有日期錯置，所以 `analysis` 只能當參考。
- **美股財報只有約 5 季**（Yahoo 的限制）；SEC EDGAR 的檔案超過 egress 1 MiB 上限，而且會擋平台固定的 User-Agent。
- **ETF 沒有財報**：stock-fundamental 會回空的 `quarterly` 並寫 warning，Run 仍成功。
- 圖表標題只用英文：容器裡沒有 CJK 字型。
- 這不是投資建議，每個輸出都有 `disclaimer`。

## 本機先測

```bash
pip install -e packages/sdk "langgraph>=0.2.50" "langchain-openai>=0.2" pandas mplfinance six
```

用 [agent-development.md](../../specs/agent-development.md) §本機先測邏輯 的假 ctx，再加一個用 httpx 實作
`ctx.http.get()` 的物件即可。沒有 LLM 時，`use_llm=false` 的 chart / technical / fundamental 都能在本機跑。
