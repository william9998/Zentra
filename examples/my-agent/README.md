# my-agent —— 從這裡開始寫你自己的 Agent

一個**真的會動**的文字分析 Agent：給它一段文字，回傳字數、句數、
平均長度與關鍵詞。複製這個目錄，改 `agent.py` 就是你的了。

```
examples/my-agent/
  agent.py      你的 Agent。改這個。
  Dockerfile    兩行，不用動。
```

## 跑一次

**build context 是這個目錄**，所以要先 `cd` 進來：

```bash
cd examples/my-agent
docker build -t localhost:6000/my-agent:1.0.0 .
docker push  localhost:6000/my-agent:1.0.0
```

接著印出可以直接註冊的完整 reference：

```bash
docker inspect --format='{{index .RepoDigests 0}}' localhost:6000/my-agent:1.0.0
# → localhost:6000/my-agent@sha256:40e064517f42…
```

> **不要貼 `docker push` 的輸出。** 它印的是 `1.0.0: digest: sha256:… size: …`，
> 只有 digest，少了前面的 repository —— 貼進去會被擋下來。
> 也不要用 `docker build` 印的那串 sha256，那是本機 image ID，是另一個識別。

註冊與執行見[根目錄 README 的 Ship an agent](../../README.md#ship-an-agent)。

試試看：

```bash
curl -s -X POST "http://localhost:8000/v1/agents/$AID/runs:invoke" \
  -H 'Content-Type: application/json' \
  -d '{"entrypoint":"default","input":{"text":"Zentra runs every agent inside an isolated sandbox. The sandbox has no route to the internet.","top_n":3}}'
```

```json
{
  "word_count": 19, "unique_words": 17, "sentences": 2,
  "avg_word_length": 4.63, "avg_sentence_length": 9.5,
  "keywords": ["sandbox", "zentra", "runs"],
  "verdict": "19 個字、2 句，主題看起來是：sandbox、zentra、runs"
}
```

## 這支示範了什麼

| | 為什麼重要 |
|---|---|
| **多節點 + 條件分支** | 文字太短就跳過關鍵詞抽取，走另一條路。這才是用 graph 而不是用一個函式的理由，Run 觀測頁也會如實顯示走了哪幾個節點 |
| **`ctx.logger` / `ctx.emit`** | 寫出去的東西會變成 Run 事件流裡的 `log` 與 `custom` 事件。**不要用 `print`** —— 那只進容器日誌，Console 上看不到 |
| **中間產物不放 state** | 平台把最終 state 整個當成 output 回傳，`output_schema` 只驗證不過濾。把切好的詞陣列放進 state，使用者就會看到幾十筆雜訊 |
| **回傳未 compile 的 graph** | 平台要自己 compile 才能注入事件 hook。你先 `.compile()` 了，Run 觀測頁就沒有 `node.start` / `node.end` 可看 |

## 改成你自己的

1. 改 `@agent(...)` 的 `name` 與 `entrypoints`
   —— `entrypoints` 是**唯一**的 schema 來源，REST 驗證、MCP `tools/list`、
   per-agent OpenAPI 全部從它生成
2. 改節點裡的邏輯
3. 發新版本時 `semver` 要換 —— 同一個 semver 重上會 `409`（版本不可變）

## 三個會讓你卡住的地方

都會在驗證階段變成 `BUILD_FAILED`，完整 traceback 在 `error.message`：

| 寫法 | 結果 |
|---|---|
| `def build()` | 被擋下，訊息會直接告訴你要 `def build(ctx: ZentraContext)` |
| 回傳 `.compile()` 過的 graph | 事件 hook 注入不進去 |
| 自己寫 Dockerfile 但漏裝 `jsonschema` | 啟動自檢會說少了什麼。用 base image 就不會發生 |

## 沙盒裡沒有的東西

- 任何 API key、LLM 金鑰
- PostgreSQL、Redis
- 對外網路

這是刻意的（P4）。沙盒掛在沒有對外路由的網段上，唯一對端是平台的 Internal Gateway。
呼叫 LLM 要走 `ctx.llm()`，而**那個還沒實作**（ZEN-002）——
所以現在能做的是不需要外部資源的工作：運算、資料轉換、流程控制。這支就是。
