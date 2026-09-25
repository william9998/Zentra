"""會呼叫 LLM 的 Agent —— ZEN-002 的示範。

跟 `examples/my-agent/` 的差別只有一行：`ctx.llm()`。
金鑰的事你完全不用管，而那正是重點。

## 金鑰在哪

**不在這裡，也不在你的 image 裡，更不在沙盒裡。**

    你的程式碼            Internal Gateway            供應商
    ──────────            ────────────────            ──────
    ctx.llm()         ──▶ 驗 run token
      api_key =           換上真金鑰            ──▶    OpenAI 相容端點
      run token           計量 token
                          寫 llm_calls
                      ◀── 原樣回傳              ◀──

沙盒的 env 只有三個變數：`ZENTRA_RUN_TOKEN`、`ZENTRA_GATEWAY`、`ZENTRA_RUN_ID`。
沒有 `OPENAI_API_KEY`，而且沙盒網段沒有對外路由 —— 就算你把金鑰硬寫進程式碼，
也打不出去（P4）。

## 這支做什麼

給它一段文字，它：
  1. 先做本地分析（不花錢的事不要叫模型做）
  2. 用 LLM 產生摘要
  3. 文字太短就跳過 LLM —— 條件分支同時也是省錢的地方
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from zentra_sdk import agent

MIN_WORDS_FOR_LLM = 8


class State(TypedDict, total=False):
    text: str
    style: str
    word_count: int
    summary: str
    used_llm: bool


@agent(
    name="llm-agent",
    version="1.0.0",
    entrypoints=[{
        "name": "default",
        "description": "用 LLM 摘要一段文字；太短的文字直接回傳原文",
        "input_schema": {
            "type": "object",
            "required": ["text"],
            "properties": {
                "text": {"type": "string", "minLength": 1, "description": "要摘要的文字"},
                "style": {
                    "type": "string",
                    "description": "摘要風格",
                    "enum": ["一句話", "條列", "白話"],
                    "default": "一句話",
                },
            },
        },
        "output_schema": {
            "type": "object",
            "required": ["summary", "word_count", "used_llm"],
            "properties": {
                "summary": {"type": "string"},
                "word_count": {"type": "integer"},
                "used_llm": {"type": "boolean", "description": "有沒有真的呼叫模型"},
            },
        },
    }],
)
def build(ctx) -> StateGraph:
    graph = StateGraph(State)

    async def measure(state: State) -> dict:
        count = len(state["text"].split())
        ctx.logger.info("量過長度", words=count)
        return {"word_count": count}

    def route(state: State) -> str:
        # 短文字叫模型摘要是浪費 —— 分支在這裡同時是產品決策與成本決策
        return "call_llm" if state["word_count"] >= MIN_WORDS_FOR_LLM else "too_short"

    async def summarise(state: State) -> dict:
        style = state.get("style") or "一句話"
        ctx.emit("llm_start", {"style": style})

        # ── 這就是全部。沒有金鑰、沒有 base_url、沒有 client 設定。──
        llm = ctx.llm()
        reply = await llm.ainvoke(
            f"用「{style}」的風格摘要以下文字，只回摘要本身：\n\n{state['text']}"
        )

        summary = reply.content.strip()
        ctx.logger.info("摘要完成", chars=len(summary))
        return {"summary": summary, "used_llm": True}

    async def too_short(state: State) -> dict:
        ctx.logger.warning("文字太短，不呼叫模型", words=state["word_count"])
        return {"summary": state["text"], "used_llm": False}

    graph.add_node("measure", measure)
    graph.add_node("summarise", summarise)
    graph.add_node("too_short", too_short)

    graph.add_edge(START, "measure")
    graph.add_conditional_edges(
        "measure", route,
        {"call_llm": "summarise", "too_short": "too_short"},
    )
    graph.add_edge("summarise", END)
    graph.add_edge("too_short", END)

    return graph
