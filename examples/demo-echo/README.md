# demo-echo —— 最小的 Zentra Agent image

一個能跑完整條 ZEN-008 流程的示範：**註冊 → 驗證 → 發布 → 執行**。
把這個目錄複製一份改成你自己的 Agent。

> 這份是照著實際跑通的流程寫的。以下每一步都在單機 compose 上驗證過。

---

## 先理解一件事：平台不收你的 image 檔案

Zentra **不接受上傳 `.tar`、不接受 `docker save`、不接受 tag**。
你自己把 image push 到 registry，平台只收一個 **digest**：

```
docker build → docker push → 在 Console 填 semver + repo@sha256:... → 平台自己 pull
```

原因是 P5（版本不可變）：`version_id` 就是 image digest 的前 12 碼。
image 不可變、不重傳，發布後也不會因為有人動了 tag 而換掉內容。
執行時平台只按 digest 拉，**永遠不解析 `latest`**。

---

## 跑一次

### 1. 準備一個 registry

正式環境用你自己的（Docker Hub、GHCR、企業 registry）。本機試用起一個就好：

```bash
docker run -d --name zentra-registry -p 5010:5000 registry:2
```

`localhost` 開頭的 registry Docker 預設允許不走 TLS，所以本機不用處理憑證。

### 2. build 並 push

**build context 是 repo 根目錄**（Dockerfile 要複製 `packages/sdk`）：

```bash
docker build -f examples/demo-echo/Dockerfile -t localhost:5010/demo-echo:1.0.0 .
docker push localhost:5010/demo-echo:1.0.0
```

push 的輸出會給你 digest，長這樣：

```
1.0.0: digest: sha256:ce3605d5b3fd... size: 2200
```

**把那個 `sha256:...` 抄下來**，下一步要用。

### 3. 建立 Agent 並註冊版本

Console：`http://localhost:8000/console/agents` → 建立 Agent → 註冊新版本，
填 semver 與 `localhost:5010/demo-echo@sha256:...`。

或用 API：

```bash
AID=$(curl -s -X POST http://localhost:8000/v1/agents \
  -H 'Content-Type: application/json' \
  -d '{"name":"demo-echo","description":"示範"}' | jq -r .agent_id)

curl -s -X POST "http://localhost:8000/v1/agents/$AID/versions" \
  -H 'Content-Type: application/json' \
  -d "{\"semver\":\"1.0.0\",\"image_ref\":\"localhost:5010/demo-echo@sha256:...\"}"
```

回應是 `BUILDING`。平台會非同步 pull image、在**一個隔離的一次性沙盒**裡跑
`zentra-agent validate`，抽出 entrypoints schema，然後轉成 `PUBLISHED`。

查狀態：

```bash
curl -s "http://localhost:8000/v1/agents/$AID/versions/1.0.0"
```

成功會看到：

```json
{
  "status": "PUBLISHED",
  "artifact": {
    "runtime_contract": "zentra.image/v1",
    "platform": "linux/arm64/v8",
    "size_bytes": 219437944
  },
  "entrypoints": [{ "name": "default", ... }]
}
```

失敗會是 `FAILED`，而且 `error.message` **帶著驗證容器裡的完整 traceback** ——
那是你唯一的除錯線索，好好看。

### 4. 執行

三個門面都可以，跑的是同一條路：

```bash
# REST（同步，上限 60 秒）
curl -s -X POST "http://localhost:8000/v1/agents/$AID/runs:invoke" \
  -H 'Content-Type: application/json' \
  -d '{"entrypoint":"default","input":{"question":"哈囉"}}'

# MCP
curl -s -X POST "http://localhost:8000/mcp/agents/$AID" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"name":"default","arguments":{"question":"哈囉"}}}'
```

Console 則是在 Agent 詳情頁選 entrypoint 發起，然後在 Run 觀測頁看事件流。

---

## 三個會讓你卡住的地方

這三個都是我們實際踩過的，契約文件目前沒寫清楚：

### 1. `build()` 要收一個參數

```python
def build(ctx) -> StateGraph:      # ✅
def build() -> StateGraph:         # ❌ TypeError: build() takes 0 positional arguments
```

runtime 呼叫的是 `build(context)`。

### 2. `jsonschema` 必須裝

SDK 的 `image_runtime` 需要它。少了會在 import 階段就死：

```
ModuleNotFoundError: No module named 'jsonschema'
```

### 3. 回傳**未 compile** 的 `StateGraph`

不要自己 `.compile()`。平台要拿未 compile 的 graph 才能注入事件 hook ——
node 的開始/結束事件就是這樣來的。你 compile 過了，Run 觀測頁就沒有事件可看。

---

## 這個 image 裡有什麼

| 檔案 | 作用 |
|---|---|
| `agent.py` | 你的 Agent。`@agent(...)` 宣告 entrypoints，`build(ctx)` 回傳 `StateGraph` |
| `Dockerfile` | 契約要求的：`io.zentra.image-contract` label、`/opt/zentra/bin/zentra-agent`、uid 65534 |

`@agent` 裝飾器裡的 `entrypoints` 是**唯一的 schema 來源**：
REST 的 `input` 驗證、MCP 的 `tools/list`、per-agent 的 OpenAPI 全部從它生成（P3）。
改了它就要發新版本 —— 同一個 semver 不能重用（P5）。

## 沙盒裡你拿不到什麼

不要在 Agent 程式碼裡找這些，它們**刻意不在**（P4）：

- 任何 API key、LLM 金鑰、registry 憑證
- PostgreSQL / Redis 連線
- 對外網路

沙盒掛在一個沒有對外路由的網段上，唯一的對端是平台的 Internal Gateway。
要呼叫 LLM 請用 `ctx.llm`（ZEN-002 提供），由平台代持金鑰。
