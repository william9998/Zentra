# node-agent：不用 Python 的單位

這個範例用 Node.js 寫一個單位，證明平台不限語言：只要照 `specs/runtime.md` 的容器約定做就能發布。它沒有任何 npm 套件依賴，只用 Node 內建的 `fetch`。

```
examples/node-agent/
  agent.js          你的單位：入口定義與每個入口要做的事。改這個。
  zentra-agent.js   約定的實作：發布檢查、取工作、回報事件與結果。一般不用動。
  package.json      讓 Node 用 ES modules。
  Dockerfile        不用 zentra/agent-base。
```

## 跑一次

```bash
cd examples/node-agent
docker build -t localhost:6000/node-agent:1.0.0 .
docker push  localhost:6000/node-agent:1.0.0
```

Console 顯示 `PUBLISHED` 後呼叫：

```bash
curl -s -X POST localhost:8000/v1/agents/<agent_id>/runs:invoke \
  -H 'content-type: application/json' -d '{"input":{"name":"小明"}}'
# → status SUCCEEDED, output {"greeting":"你好，小明！"}

curl -s -X POST localhost:8000/v1/agents/<agent_id>/runs:invoke \
  -H 'content-type: application/json' \
  -d '{"entrypoint":"word_count","input":{"text":"hello zentra 你好"}}'
# → output {"characters":15,"words":2}
```

## 在 `agent.js` 裡能用的 `ctx`

| 用法 | 說明 |
|---|---|
| `await ctx.log("info", "msg", {key: value})` | 進事件流 |
| `await ctx.emit("name", {...})` | 自訂事件 |
| `await ctx.step("名稱", async () => {...})` | 前後送 `node.start` / `node.end` |
| `ctx.config.X` | Console 設定，明文 |
| `ctx.secretNames` | 機密只有名字，沒有值 |
| `await ctx.llm({messages: [...]})` | OpenAI 相容 chat/completions，經平台代打，不用金鑰 |
| `await ctx.http({url, method})` | 經平台代打外部 HTTP，要先在 Console 加白名單 |

換成別的語言時，照 `zentra-agent.js` 的結構改寫即可：三種模式（`validate`、`serve`、`run`）、幾支 HTTP 呼叫，沒有別的。
