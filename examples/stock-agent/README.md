# stock-agent —— 帶 retry 迴圈、會上網搜尋的股市分析 Agent

問任何股票或大盤問題，它判斷你問的是個股還是大盤、上網找資料、
**自己檢查找到的東西夠不夠**，不夠就換個問法再找，最後給看多/看空比例與買賣建議。

```
classify ─▶ research ─▶ assess ─┬─(不足且還有次數)─▶ research（帶著「缺什麼」重搜）
                                └─(夠了 或 用完次數)──▶ advise ─▶ END
```

**回頭的那一條就是 retry 迴圈**，也是用 graph 而不是一串函式呼叫的理由。

## 跑一次

```bash
cd examples/stock-agent
docker build -t localhost:6000/stock-agent:1.0.0 .
docker push  localhost:6000/stock-agent:1.0.0
docker inspect --format='{{index .RepoDigests 0}}' localhost:6000/stock-agent:1.0.0
```

註冊之後在 Agent 詳情頁設定 LLM 憑證（它需要一把能上網搜尋的金鑰），然後：

```json
{"question": "台積電最近怎麼樣，現在可以買嗎？"}
```

```json
{
  "subject_type": "STOCK",
  "subject": "TSMC 台積電",
  "summary": "月營收年增 25%，3nm 滿載，外資上調目標價；地緣政治為主要風險。",
  "bullish_percent": 70,
  "bearish_percent": 30,
  "recommendation": "BUY",
  "rationale": "基本面成長明確，風險屬外部且已部分反映。",
  "sources": ["https://…", "https://…"],
  "rounds": 2,
  "data_quality": "GOOD",
  "disclaimer": "…"
}
```

大盤問題（`{"question": "美股大盤現在能買嗎"}`）走同一條路，`subject_type` 是 `MARKET`。

## 網路搜尋怎麼運作

**不是沙盒上網** —— 沙盒沒有對外路由（P4）。是**模型在供應商那一側搜**：

```
沙盒 ──▶ Internal Gateway ──▶ 供應商（它去搜網路）
         換上真金鑰、計量
```

OpenAI 有兩種形狀，能用哪一種取決於你的 model，所以 agent **依序試**：

| 形狀 | 走哪條 API | 特性 |
|---|---|---|
| `web_search` tool | `/responses` | 模型自己決定何時搜，較靈活 |
| `web_search_options` | `/chat/completions` | 只有 `*-search` 系列模型吃 |

平台的 Gateway 兩條都代理（白名單，見 `llm.UPSTREAM_PATHS`）。
兩種都失敗就不帶搜尋硬問一次 —— **不讓 Run 死**，而是讓 `assess` 判定資料不足，
最後由 `data_quality` 誠實回報 `INSUFFICIENT`。

> **關於 model 名稱**：平台把 `model` 字串原樣轉給供應商，不做任何白名單。
> 所以能不能用某個型號，取決於你的金鑰對應的帳號有沒有那個模型 ——
> 平台這邊不需要改任何東西。

## retry 迴圈的三個設計重點

**1. 一定要有上限。** `MAX_RESEARCH_ROUNDS = 3`。沙盒有 15 分鐘 wall-clock 硬上限
（SPEC §10），而每一輪都是一次帶網路搜尋的 LLM 呼叫 —— 很慢也很貴。
沒有上限的條件邊界就是一個會燒錢的無窮迴圈。

**2. 判斷交給模型，不是寫死規則。** `assess` 節點問模型「這些資料夠不夠回答使用者的問題」，
不夠時要它說出**缺什麼**，下一輪把那句話帶進 prompt。寫死「至少要 3 個來源」
那種規則擋不住「找到 5 篇但都是別家公司的新聞」。

**3. 用完次數要誠實。** 到上限仍不滿意時不會失敗，而是用手上的資料作答，
並把 `data_quality` 標成 `PARTIAL`，`rounds` 等於上限。看到這兩個訊號就知道結論的底氣不足。

## 輸出裡的誠實訊號

| 欄位 | 看什麼 |
|---|---|
| `rounds` | 等於 3（上限）表示最後一輪仍不滿意 |
| `data_quality` | `INSUFFICIENT` 表示資料撐不起結論 |
| `recommendation` | `HOLD` 表示**資料不足以給方向**，不是「持平看好」 |
| `sources` | 空陣列表示沒真的搜到東西 |

## ⚠️ 這不是投資建議

輸出固定帶 `disclaimer`。模型看得到的只有它搜到的那些公開資訊，
不知道你的持倉、成本與風險承受度，也可能引用過時或錯誤的來源。

把它當成「把公開資訊整理成固定格式」的工具，不是判斷依據。

## 這支示範了什麼

| | |
|---|---|
| **條件邊界回頭** | `add_conditional_edges` 指回上游節點，這是 LangGraph 的核心能力 |
| **有界的迴圈** | 計數器 + 上限檢查放在 route 函式裡 |
| **多層退化** | Responses API → chat/completions → 不帶搜尋，逐層退而不崩 |
| **`ctx.emit` 追蹤進度** | Run 觀測頁看得到 `research_round` / `assessed` 每一輪的判斷 |
| **State 要宣告完整** | ⚠️ 沒宣告在 `State` 裡的 key **LangGraph 會安靜丟掉**，最後在輸出驗證才爆。這支寫的時候就踩過 |
