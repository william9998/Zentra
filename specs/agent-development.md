# Agent 開發指南

> 給要在 Zentra 上寫 Agent 的開發者：**可以怎麼寫、不能怎麼寫、寫完怎麼放上來跑。**
> 想知道平台收到 image 之後怎麼驗證、發布，看 [agent-registry.md](agent-registry.md)；
> 想知道 Agent 的對外連線、LLM、寄信怎麼運作，看 [gateway.md](gateway.md)。

## 先看全貌

一個 Agent = **一個 Python 檔（`agent.py`）+ 一個兩行的 Dockerfile**。

```
你寫的 agent.py                    平台做的事
───────────────                    ─────────────────────────
@agent(...) 宣告名稱與入口   ──▶    驗證你寫的格式對不對（註冊時）
def build(ctx): 回傳一張 graph ──▶  替你 compile、接上事件、在容器裡執行
                                   REST API / MCP 的輸入驗證與說明，全部從你宣告的入口生出來
```

你**不用**寫：HTTP 伺服器、認證、排程、重試、事件推送、API 文件。這些平台都做了。
你**不能**做：直接上網、讀 LLM 金鑰、讀資料庫。這些要透過 `ctx` 請平台代勞（看 §ctx 能做什麼）。

## 五分鐘跑起來

照 `examples/my-agent/` 複製一份，這是最小的可用範例。

**1. `agent.py`**（最小可用版本）

```python
from typing import TypedDict
from langgraph.graph import END, START, StateGraph
from zentra_sdk import agent


class State(TypedDict, total=False):
    name: str        # 輸入
    greeting: str    # 結果


@agent(
    name="hello-agent",
    version="1.0.0",
    entrypoints=[{
        "name": "default",
        "description": "跟指定的人打招呼，回傳一句問候語",
        "input_schema": {
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string", "description": "對方的名字"}},
        },
        "output_schema": {
            "type": "object",
            "required": ["greeting"],
            "properties": {"greeting": {"type": "string"}},
        },
    }],
)
def build(ctx) -> StateGraph:
    graph = StateGraph(State)

    async def greet(state: State) -> dict:
        ctx.logger.info("開始打招呼", name=state["name"])
        return {"greeting": f"你好，{state['name']}！"}

    graph.add_node("greet", greet)
    graph.add_edge(START, "greet")
    graph.add_edge("greet", END)
    return graph          # 注意：不要 compile
```

**2. `Dockerfile`**

```dockerfile
FROM zentra/agent-base:1.0
COPY agent.py /opt/zentra/app/agent.py
```

**3. build、push（push 就是註冊）**

```bash
docker build -t zentra/agent-base:1.0 -f docker/agent-base.Dockerfile .   # 第一次需要，在 repo 根目錄
cd my-agent-dir
docker build -t localhost:6000/hello-agent:1.0.0 .
docker push localhost:6000/hello-agent:1.0.0
```

**4. 到 Console 看**：`http://localhost:8000/console/agents`，版本會從「驗證中」變成「已發布」，然後就可以呼叫。

```bash
curl -s -X POST localhost:8000/v1/agents/$AID/runs:invoke \
  -H 'content-type: application/json' \
  -d '{"input":{"name":"小明"}}'
# → output: {"name":"小明","greeting":"你好，小明！"}
```

注意 output 裡**連輸入的 `name` 也在**，原因看 §輸出就是最終 state。

## 寫 Agent 的規則

違反的話，通常在「註冊驗證」就會被擋下來，版本變成「驗證失敗」，錯誤訊息會告訴你原因。

### 1. 檔案與函式

| 規則 | 說明 |
|---|---|
| 檔案放 `/opt/zentra/app/agent.py` | 平台固定去 import 一個叫 `agent` 的模組。檔名在你那邊可以不同，但 COPY 進 image 之後要叫 `agent.py` |
| 要有一個叫 `build` 的函式 | 用 `@agent(...)` 裝飾 |
| `build` **只收一個參數**（ctx） | 寫成 `def build()` 會被擋 |
| `build` 回傳**還沒 compile** 的 `StateGraph` | 不要呼叫 `.compile()`。平台要自己 compile，才能接上事件 |
| 只能用 LangGraph | 不支援別的 agent 框架（P2） |

### 2. `@agent(...)` 要寫什麼

```python
@agent(name="hello-agent", version="1.0.0", entrypoints=[ ... ])
```

- `name`、`version`：寫你的 Agent 名稱和版本。
  **平台實際用的是你註冊時給的名稱（push 的 repository 名）和版號（push 的 tag）**，不是這裡寫的。
  我沒有在程式碼裡找到「兩邊必須一致」的檢查，但請自己保持一致，免得日後對不上。
- `entrypoints`：**必填**，是一個清單。下一節詳細說明。

### 3. entrypoint：這個 Agent 對外開放的入口

**這是你自己在程式碼裡定義的**，平台只負責檢查格式。

每個 entrypoint 就是 Agent 對外的一個「功能」：REST 會用它的格式驗證輸入，MCP 會把它變成一個 tool。

| 規則 | 說明 |
|---|---|
| 至少一個 | 空清單會被擋 |
| `name` 不能重複 | |
| **欄位恰好是這四個，不能多、不能少** | `name`、`description`、`input_schema`、`output_schema`。多寫一個欄位會被擋 |
| `input_schema` / `output_schema` | 必須是合法的 JSON Schema（Draft 2020-12），而且**最外層 `type` 必須是 `"object"`** |

**呼叫時選哪個入口：** REST 用 body 的 `entrypoint`（省略 = `default`）；MCP 則是不同的 tool。

**`description` 很重要。** 它會顯示在 OpenAPI 文件，也是 **MCP 的 AI 平台判斷「要不要呼叫這個 tool」的唯一依據**。
欄位上的 `description`（例如 `"description": "例如「台積電最近怎麼樣」"`）會幫對方的 LLM 把參數填對。寫清楚「能做什麼、輸入長什麼樣」。

```python
# ❌ LLM 看不出能幹嘛
"description": "處理資料"

# ✅
"description": "問任何股票或大盤問題，回新聞面整理、看多/看空比例與買賣建議"
```

**什麼時候需要多個 entrypoint？通常不需要。** 範例 Agent 全部都只有一個 `default`。

⚠️ 一個重要的限制：**不管呼叫哪個 entrypoint，跑的都是同一個 `build()` 產生的同一張 graph**，
而且 graph 拿不到「我是被哪個入口叫的」，只拿得到 `input`。
所以多個 entrypoint 只是「不同的輸入／輸出格式 + 不同的說明」。要讓它們做不同的事，得自己在 `input` 裡加一個欄位（例如 `mode`）、在 graph 裡分流。
**想讓 AI 平台分得清楚兩種功能，更直接的做法是拆成兩個 Agent。**

### 4. 輸入怎麼進來、輸出怎麼出去

```
呼叫端送 input ──▶ 平台用 input_schema 驗證 ──▶ input 變成 graph 的「初始 state」
                                                        │ 節點一個個跑，每個節點回傳要更新的欄位
                                                        ▼
        output ◀── 平台用 output_schema 驗證 ◀── graph 跑完的「最終 state」整份
```

**輸出就是最終 state。** `output_schema` 只做驗證，**不做過濾**：

- 你放進 state 的所有欄位，都會出現在 output 裡（包含輸入欄位）。
- 所以**中間產物不要放進 state**（例如切好的一大堆詞），不然 output 會充滿雜訊。需要的話在節點裡重算，或用區域變數。
- `output_schema` 的 `required` 欄位如果沒有出現在最終 state，這筆 Run 會失敗。

**State 的 key 要和輸入欄位名對得上。** 例如 `input_schema` 有 `question`，State 裡就要有 `question`，節點才讀得到 `state["question"]`。

### 5. 在 `build()` 裡不能做的事

驗證時平台會**呼叫一次 `build()`**，而且那時：

- `ctx.config` 是空的（`{}`），`ctx.secret_names` 是空的；
- **沒有 run token**，所以 `ctx.llm()`、`ctx.http`、`ctx.send_email()` 都不能用。

所以：

| ❌ 不要在 `build()` 裡 | ✅ 改成 |
|---|---|
| `ctx.config["MAX_ROUNDS"]`（缺 key 就爆） | `ctx.config.get("MAX_ROUNDS", 3)`，或在節點裡讀 |
| 呼叫 `ctx.llm()` 然後立刻 `invoke` | 在**節點函式**裡呼叫 |
| 連網、讀檔、做耗時的初始化 | 放進節點 |

為什麼要這麼嚴：如果 `build()` 在空設定下就會爆，那哪天有人把設定清掉，這個 Agent 的每一筆 Run 都會失敗，但版本狀態仍然顯示「已發布」。驗證時刻意用空設定，就是要提早抓到這種問題。

## `ctx` 能做什麼

`ctx` 是平台交給 `build()` 的工具箱。**這是 Agent 和外面世界唯一的橋。**

| 你想做的事 | 用法 | 說明 |
|---|---|---|
| 呼叫 LLM | `llm = ctx.llm()`<br>`reply = await llm.ainvoke("…")` | 回傳標準的 LangChain chat model。**不用也不能自己填金鑰**，平台代打並計費。想換模型：`ctx.llm("gpt-4o", temperature=0)`。不指定就用這個 Agent 在 Console 設的預設 model |
| 讀設定 | `ctx.config.get("MAX_ROUNDS", 3)` | Console「設定」那份 JSON。明文，**改了不用發版**，下一筆 Run 生效 |
| 知道有哪些機密 | `"SMTP_PASSWORD" in ctx.secret_names` | **只拿得到名字，拿不到值**。用來提早檢查「缺了什麼」，在第一個節點就報錯 |
| 對外打 HTTP | `ctx.http.get(url).raise_for_status().json()` | 平台代打，要先在 Console 加白名單規則，沒規則就一個網站都連不到 |
| 寄信 | `ctx.send_email(to="me@x.com", subject="…", text="…")` | 寄件者與收件人名單要先在 Console 設好 |
| 寫 log | `ctx.logger.info("訊息", key=value)` | 會出現在 Run 觀測頁的事件流。**不要用 `print`**：stdout 只進容器日誌，Console 看不到 |
| 回報進度 | `ctx.emit("research_round", {"round": 2})` | 自訂事件，Run 觀測頁看得到 |
| 這筆 Run 的 id | `ctx.run_id` | |

**沒有，也不會有**：`ctx.secrets`（直接取機密的值，永久否決）、`ctx.tools`、`ctx.knowledge`。

### 呼叫 LLM

```python
async def summarize(state):
    llm = ctx.llm()                                   # 用 Agent 的預設 model
    reply = await llm.ainvoke(f"用一句話摘要：{state['text']}")
    return {"summary": reply.content}
```

- 不要自己用 `requests` / `openai` 打外網：容器**沒有對外網路**，會直接失敗。
- 每次呼叫都有紀錄（`llm_calls`），token 數和費用是平台算的。
- 沒有 LLM 可以用？到 Console 的「LLM 憑證」設這個 Agent 的金鑰，沒設就用平台預設金鑰。

### 連外部 API（例如查交易所）

兩步：

**① 在 Console →「對外連線」加白名單規則**（沒做這步，一定被擋）：

```json
{ "host": "api.binance.com", "methods": ["GET"], "path_prefix": "/api/v3/" }
```

**② 程式碼裡用 `ctx.http`**，而且要寫在**同步節點**（`def`，不是 `async def`）：

```python
def fetch_price(state):                       # 注意：def，不是 async def
    resp = ctx.http.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT")
    if resp.status != 200:                    # 上游回 4xx/5xx 不會拋例外，要自己看 status
        return {"error": f"上游回 {resp.status}"}
    return {"price": resp.json()["price"]}
```

- `ctx.http` 是**同步阻塞**的。寫在 `async def` 節點裡，會卡住同一個容器裡其他 Run。
- 被平台擋下（沒規則、host 不在名單…）會拋 `EgressBlocked`：**重試沒用，要去改 Console 的設定**。
- 上游自己回錯誤碼不會拋例外，你自己判斷（這樣才寫得出「主要來源失敗就換備援」）。
- 需要認證（API key）？不要自己帶 `Authorization`（會被擋）。把 key 存進 Console 的「機密」，再在白名單規則上設 `auth_secret_name` + `auth_header`，平台代打時替你加上去，你的程式碼拿不到那個值。
- 平台固定送 `User-Agent: Zentra-Egress/1`，你改不了。

## 容器裡的限制

你的 Agent 跑在隔離容器裡，這些限制是平台硬性規定的，**沒辦法調**：

| 項目 | 限制 | 超過會怎樣 |
|---|---|---|
| 記憶體 | 2 GB，無 swap | 容器被殺（**同一個容器裡別的 Run 也會一起掛**） |
| CPU | 2 core | 變慢 |
| 單筆 Run 時間 | 預設 900 秒，**上限 900 秒** | 失敗，`SANDBOX_TIMEOUT` |
| 行程數 | 256 | fork 失敗 |
| 檔案系統 | **唯讀**，只有 `/workspace` 可寫（512 MB、不能執行檔案） | 寫入失敗 |
| 網路 | **沒有對外網路**，只連得到平台的 Gateway | 一律失敗，請改用 `ctx.llm()` / `ctx.http` |
| 開 port | 不能。容器不開任何 HTTP port | |
| 身分 | 非 root（65534），沒有特殊權限 | |
| 環境變數 | 只有平台設定的那幾個，沒有任何金鑰 | |

**多筆 Run 共用一個容器（預設最多 50 筆同時）。** 所以：

- **不要用全域變數存「這一筆 Run 的資料」**，會串到別的 Run。資料放 state。
- **不要在 `async def` 節點裡做阻塞呼叫**（`time.sleep`、同步的 `requests`、`ctx.http`），會卡住所有 Run。要阻塞就用同步節點（`def`），LangGraph 會把它丟到執行緒裡跑。
- 不要一次把超大資料（長文件、大圖）整份讀進記憶體。N 筆 Run 共用 2 GB。

## 需要額外的 Python 套件

在你的 Dockerfile 裡安裝，**在 build 時**裝好，執行時沒有網路可以裝：

```dockerfile
FROM zentra/agent-base:1.0
USER root
RUN pip install --no-cache-dir pandas==2.2.0
USER 65534:65534
COPY agent.py /opt/zentra/app/agent.py
```

base image 預設用非 root 身分，所以安裝套件前要先切 `USER root`、裝完再切回 `USER 65534:65534`。
（這點我是依 base image 的設定推斷的，沒有實際用額外套件驗證過。）

⚠️ **Dockerfile 不要宣告 `VOLUME`。** Docker 會因此建出可寫的匿名 volume，繞過唯讀檔案系統，而且容器刪掉後 volume 還留在主機上。這是已知的隔離缺陷（ZEN008-ISO-01），還沒修。

base image 已經包含：Python 3.12、`langgraph`、`langchain-openai`、`httpx`、`jsonschema`、`zentra_sdk`。

## 註冊後會發生什麼

| 你做的 | 平台做的 |
|---|---|
| `docker push localhost:6000/<名稱>:<版號>` | 自動建立（或找到）這個 Agent，註冊這個版號，開始驗證 |
| 同一個版號再 push 一次 | **取代**：新的驗證通過才切換，失敗的話舊的照常服務 |
| 換新版號 push | 新增一個版本，成為最新版，之後的呼叫用它 |

驗證會開一次性容器，**真的 import 你的 `agent.py`、呼叫一次 `build()`**，檢查：

1. image 的 label 是 `io.zentra.image-contract=zentra.image/v1`（用 base image 就有）
2. `agent` 模組能 import，有 `build`，被 `@agent` 裝飾
3. `build` 恰好收一個參數
4. `entrypoints` 格式正確
5. `build(ctx)`（空設定）能成功執行，而且回傳未 compile 的 `StateGraph`

版本狀態：**驗證中 → 已發布**，或 **驗證失敗**。失敗時在 Console 版本列表看得到 `error.message`（完整錯誤）和 `details.stage`：

| stage | 意思 | 常見原因 |
|---|---|---|
| `PULL` | 拉不到 image | digest 寫錯、registry 連不上 |
| `INSPECT` | image 檢查不過 | 缺 `io.zentra.image-contract` label、平台架構不對 |
| `VALIDATE` | 驗證你的程式碼失敗 | `build()` 簽章錯、回傳了已 compile 的 graph、entrypoints 格式錯、`build()` 在空設定下爆掉 |

修好後用**同一個版號**重新 push 即可。

## 本機先測邏輯（建議）

在送上平台前，可以先用一個假的 `ctx` 在本機跑 graph，不用每次都 build image：

先在本機安裝 SDK 和 LangGraph：`pip install -e packages/sdk`（在 repo 根目錄）。然後：

```python
class FakeLogger:
    def info(self, *a, **k): print("INFO", a, k)
    warning = error = debug = info

class FakeCtx:
    logger = FakeLogger()
    config = {}
    secret_names = []
    def emit(self, name, payload=None): print("EMIT", name, payload)

from agent import build
app = build(FakeCtx()).compile()
print(app.invoke({"name": "小明"}))      # 就是你的 input
```

這只能測「流程邏輯」。`ctx.llm()` / `ctx.http` 需要平台，本機測不了，要在平台上實際跑。（這個做法是建議，不是平台提供的工具。）

## 常見錯誤

| 症狀 | 原因 | 怎麼辦 |
|---|---|---|
| 驗證失敗：`build must accept exactly one positional argument` | 寫成 `def build():` | 改成 `def build(ctx):` |
| 驗證失敗：`must return an uncompiled StateGraph` | 在 `build` 裡呼叫了 `.compile()` | 直接 `return graph` |
| 驗證失敗：`@agent must declare a non-empty entrypoints list` | `@agent` 沒寫 `entrypoints` | 補上 |
| 驗證失敗：`each entrypoint must contain name, description, input_schema, output_schema` | 欄位多了或少了 | 剛好這四個 |
| 驗證失敗：`entrypoint input/output schemas must describe objects` | schema 最外層不是 `"type": "object"` | 改成 object |
| 驗證失敗：`build()` 裡 `KeyError` | 在 `build()` 裡 `ctx.config["x"]` | 用 `.get()`，或移到節點 |
| 驗證失敗：`ctx.llm() 只能在 Run 的沙盒裡呼叫` | 在 `build()` 裡呼叫了 `ctx.llm()` / `ctx.http` | 移到節點函式裡 |
| Run 失敗：`EGRESS_BLOCKED` | 沒加白名單規則，或不符合規則 | Console →「對外連線」加規則，看 `details.reason` |
| Run 失敗：output 驗證錯誤 | 最終 state 缺了 `output_schema` 的必填欄位 | 檢查節點有沒有回傳該欄位 |
| Console 看不到 `print` 的輸出 | stdout 不會進事件流 | 改用 `ctx.logger.info(...)` |
| 連外網失敗（timeout / connection refused） | 容器沒有對外網路 | 改用 `ctx.llm()` / `ctx.http` |
| 改了設定沒生效 | 在 `build()` 裡讀了 config 並存起來 | 設定每筆 Run 重讀，在節點裡讀 `ctx.config` |

## 範例

| 目錄 | 示範什麼 |
|---|---|
| `examples/my-agent/` | **起點**。多節點 + 條件分支、`ctx.logger` / `ctx.emit`、最小 Dockerfile |
| `examples/llm-agent/` | 呼叫 LLM（`ctx.llm()`），沒有金鑰也能寫 |
| `examples/stock-agent/` | 完整的實戰例子：多輪搜尋、判斷是否足夠、可重複的流程 |
| `examples/crypto-quant-agent/` | 連外部 API（`ctx.http`）+ 寄信（`ctx.send_email()`） |
| `examples/demo-echo/` | 最簡單的回聲 Agent，驗證流程用 |
| `examples/probe-agent/` | 平台的測試 Agent（`make smoke`、壓測打它）。`asyncio.sleep` 模擬等待外部服務、用 input 的 `mode` 在同一張 graph 裡分流 |

## 開發前檢查清單

- [ ] `agent.py` 有 `@agent(name, version, entrypoints=[…])` 和 `def build(ctx)`
- [ ] `entrypoints` 每一項剛好有 `name`、`description`、`input_schema`、`output_schema`，兩個 schema 最外層都是 `object`
- [ ] `description` 寫得夠清楚（AI 平台靠它選 tool）
- [ ] `build()` 回傳**未 compile** 的 `StateGraph`
- [ ] `build()` 裡沒有 `ctx.llm()` / `ctx.http` / 對 `config` 的硬性要求
- [ ] State 只放輸入與最終結果，沒有中間大陣列
- [ ] 需要連外 API 的，在**同步節點**用 `ctx.http`，並且 Console 已加白名單
- [ ] 需要密碼 / key 的，存進 Console「機密」，不寫在程式碼或設定裡
- [ ] 沒有用 `print`、沒有全域變數存單筆 Run 的資料
- [ ] Dockerfile 沒有 `VOLUME`
