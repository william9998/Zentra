# llm-agent —— 會呼叫 LLM 的 Agent

跟 [`../my-agent/`](../my-agent/) 的差別只有一行：**`ctx.llm()`**。
金鑰的事你完全不用管，而那正是重點。

## 金鑰在哪（P4）

```
你的程式碼              Internal Gateway              供應商
──────────              ────────────────              ──────
ctx.llm()           ──▶ 驗 run token
  api_key =             換上真金鑰              ──▶    OpenAI 相容端點
  run token             計量 token
                        寫 llm_calls
                    ◀── 原樣回傳                ◀──
```

沙盒的 env 只有三個變數：`ZENTRA_RUN_TOKEN`、`ZENTRA_GATEWAY_URL`、`ZENTRA_RUN_ID`。
**沒有 `OPENAI_API_KEY`**，而且沙盒網段沒有對外路由 ——
就算你把金鑰硬寫進程式碼也打不出去。

這不是靠紀律，是靠 `worker` 手上根本沒有金鑰：那幾個變數只宣告在 `app` 服務上
（看 `compose.yaml`）。worker 是注入沙盒 env 的那一個，它沒有的東西注入不了。

## 要先設定什麼

`app` 服務要有供應商金鑰。放在 `.env`（只有 `app` 讀得到）：

```bash
ZENTRA_LLM_API_KEY=sk-...
ZENTRA_LLM_BASE_URL=https://api.openai.com/v1     # 預設值，相容端點都可以換
ZENTRA_LLM_DEFAULT_MODEL=gpt-4o-mini              # 預設值
```

**不要**把它加進 `compose.yaml` 裡 `worker` 的 `environment`。那會讓金鑰有機會進沙盒。

### 或者：這個 Agent 用自己的金鑰

不想全部 Agent 共用一把的話，可以幫單一 Agent 註冊專屬憑證（ZEN-012）：

```bash
curl -X PUT "http://localhost:8000/v1/agents/$AID/llm-credential" \
  -H 'Content-Type: application/json' \
  -d '{"api_key":"sk-...","base_url":"https://api.openai.com/v1","default_model":"gpt-4o-mini"}'
```

**憑證綁 Agent 不綁版本**：發新版不用重填，更新則該 Agent 所有版本立刻一起變。
Gateway 的順序是「Agent 自己的 → 平台預設」。

金鑰**寫得進去讀不出來** —— `GET` 只回尾四碼：

```json
{"configured": true, "last_four": "WXYZ", "base_url": "...", "updated_at": "..."}
```

平台要先設定 `ZENTRA_SECRET_KEY`（靜態加密用），否則會拒絕儲存而不是存明文。
與 P5 的互動見 [ADR-0011](../../specs/adr/ADR-0011-per-agent-llm-credentials.md)。

沒設金鑰的話，呼叫會回一個說得清楚的錯：

```json
{"code": "LLM_ERROR",
 "message": "the platform has no LLM credential configured; set ZENTRA_LLM_API_KEY on the app service"}
```

## 跑一次

```bash
cd examples/llm-agent
docker build -t localhost:6000/llm-agent:1.0.0 .
docker push  localhost:6000/llm-agent:1.0.0
```

註冊與執行見[根目錄 README 的 Ship an agent](../../README.md#ship-an-agent)。

```bash
curl -s -X POST "http://localhost:8000/v1/agents/$AID/runs:invoke" \
  -H 'Content-Type: application/json' \
  -d '{"entrypoint":"default","input":{"text":"要摘要的長文字…","style":"一句話"}}'
```

回應會帶上平台算的用量：

```json
{
  "status": "SUCCEEDED",
  "output": {"summary": "…", "used_llm": true, "word_count": 19},
  "usage": {"llm_calls": 1, "input_tokens": 11, "output_tokens": 7, "cost_usd": "0"}
}
```

> `usage` **是平台從 `llm_calls` 表算出來的，不是你的程式碼報的。** 沙盒跑的是使用者
> 程式碼，它報的用量沒有理由被當成帳。`cost_usd` 要設定 `llm_prices` 才會非零 ——
> SPEC §11 把計費出帳列在範圍外，這裡只做計量。

## 條件分支同時是成本決策

```python
def route(state):
    return "call_llm" if state["word_count"] >= MIN_WORDS_FOR_LLM else "too_short"
```

短文字叫模型摘要是浪費。graph 讓「什麼時候該花錢」變成流程裡看得見的一個節點，
而不是藏在某個 `if` 裡面。

## 能用 OpenAI 的模型，不能用 OpenAI 的 agent 框架

這點最容易誤會：

| | 支援嗎 |
|---|---|
| 用 GPT 系列模型 | ✅ `ctx.llm()` 就是 OpenAI 相容的 |
| 換成其他相容供應商 | ✅ 改 `app` 的 `ZENTRA_LLM_BASE_URL`，你的程式碼一行都不用動 |
| 用 OpenAI Agents SDK 編排 | ❌ P2：框架統一 LangChain + LangGraph |

`build()` 必須回傳未 compile 的 `StateGraph`，runtime 會實際檢查型別。
理由與代價評估見 [ADR-0010](../../specs/adr/ADR-0010-agent-framework-lock-in.md)。
