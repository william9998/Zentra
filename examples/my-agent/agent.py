"""文字分析 Agent —— 複製這個目錄，改成你自己的。

這是一個**真的會動、也真的有用**的 Agent，不是 hello world：
給它一段文字，回傳統計、可讀性指標與關鍵詞。

它示範了五件寫 Zentra Agent 一定會用到的事：

  1. 多個節點 + **條件分支** —— 這才是用 graph 而不是用函式的理由
  2. `ctx.logger` 與 `ctx.emit` —— 讓 Run 觀測頁看得到進度
  3. state 逐節點累積
  4. 回傳**未 compile** 的 StateGraph
  5. **把中間結果留在 state 外面** —— 見下面「輸出就是最終 state」

## 輸出就是最終 state

平台把 graph 跑完之後的**整個 state** 當成 Run 的 output 回傳，
`output_schema` 只做驗證，不做過濾。所以放進 state 的東西都會出現在使用者眼前。

這支刻意**不把切好的詞陣列放進 state** —— 那是中間產物，放進去會讓輸出多出
幾十筆雜訊。需要它的兩個節點各自用 `tokenise()` 重算一次，那比污染輸出便宜。

**沒有呼叫 LLM，也沒有連外網**，因為沙盒裡本來就沒有那些東西（P4）。
`ctx.llm()` 要等 ZEN-002。現在能做的是不需要外部資源的工作 ——
運算、資料轉換、流程控制，這支就是其中之一。
"""

from collections import Counter
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from zentra_sdk import agent

# 英文停用詞。夠短所以直接寫死，正式用途請換成完整表。
STOP_WORDS = frozenset("""
a an and are as at be but by for from has have he her his i in is it its
of on or she that the their they this to was were will with you your
""".split())

MIN_WORDS_FOR_KEYWORDS = 10


def tokenise(text: str) -> list[str]:
    """切詞。刻意做成純函式而不是把結果存進 state —— 見模組 docstring。"""
    words = (token.strip(".,!?;:\"'()[]{}").lower() for token in text.split())
    return [w for w in words if w]


class State(TypedDict, total=False):
    """節點之間傳遞的狀態。

    **每個 key 都會出現在 Run 的 output 裡**，所以這裡只放輸入與最終結果，
    不放中間產物。
    """
    text: str                 # 輸入
    top_n: int                # 輸入（選填）
    sentences: int            # normalise 產出
    word_count: int           # normalise 產出（給 route 判斷用）
    unique_words: int
    avg_word_length: float
    avg_sentence_length: float
    keywords: list[str]
    verdict: str


@agent(
    name="my-agent",
    version="1.0.0",
    entrypoints=[{
        "name": "default",
        "description": "分析一段文字：字數、句數、平均長度與關鍵詞",
        "input_schema": {
            "type": "object",
            "required": ["text"],
            "properties": {
                "text": {
                    "type": "string",
                    "description": "要分析的文字",
                    "minLength": 1,
                },
                "top_n": {
                    "type": "integer",
                    "description": "回傳幾個關鍵詞",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 20,
                },
            },
        },
        "output_schema": {
            "type": "object",
            "required": [
                "word_count", "unique_words", "sentences",
                "avg_word_length", "avg_sentence_length",
                "keywords", "verdict",
            ],
            "properties": {
                "word_count": {"type": "integer"},
                "unique_words": {"type": "integer"},
                "sentences": {"type": "integer"},
                "avg_word_length": {"type": "number"},
                "avg_sentence_length": {"type": "number"},
                "keywords": {"type": "array", "items": {"type": "string"}},
                "verdict": {"type": "string"},
            },
        },
    }],
)
def build(ctx) -> StateGraph:
    """平台呼叫這個函式來取得你的 graph。

    注意三件事，錯了會在驗證階段就被擋下來：
      * 必須收一個參數（context），不能寫 `def build()`
      * 必須回傳**未 compile** 的 StateGraph —— 平台要自己 compile 才能注入事件 hook
      * 模組必須能以 `agent` 這個名字被 import（Dockerfile 已經處理好）
    """

    graph = StateGraph(State)

    # ── 節點 1：切詞與切句 ──────────────────────────────────────────

    async def normalise(state: State) -> dict:
        text = state["text"]
        ctx.logger.info("開始分析", chars=len(text))

        words = tokenise(text)
        sentences = sum(text.count(mark) for mark in ".!?。！？") or 1

        ctx.emit("normalised", {"words": len(words), "sentences": sentences})
        # 只回傳計數，不回傳詞陣列 —— 它會變成 output 的一部分
        return {"word_count": len(words), "sentences": sentences}

    # ── 條件分支：文字太短就跳過關鍵詞抽取 ──────────────────────────
    #
    # 這就是用 graph 的價值 —— 流程會依資料走不同的路，
    # 而 Run 觀測頁會如實顯示它走了哪幾個節點。

    def route(state: State) -> str:
        if state.get("word_count", 0) < MIN_WORDS_FOR_KEYWORDS:
            return "too_short"
        return "long_enough"

    # ── 節點 2：統計 ───────────────────────────────────────────────

    async def analyse(state: State) -> dict:
        words = tokenise(state["text"])
        count = len(words)

        result = {
            "unique_words": len(set(words)),
            "avg_word_length": round(sum(len(w) for w in words) / count, 2),
            "avg_sentence_length": round(count / state["sentences"], 2),
        }
        ctx.emit("analysed", result)
        return result

    # ── 節點 3：關鍵詞 ─────────────────────────────────────────────

    async def extract_keywords(state: State) -> dict:
        top_n = state.get("top_n") or 5
        meaningful = [
            w for w in tokenise(state["text"])
            if w not in STOP_WORDS and len(w) > 2
        ]
        ranked = [word for word, _ in Counter(meaningful).most_common(top_n)]

        ctx.logger.info("抽出關鍵詞", count=len(ranked))
        return {
            "keywords": ranked,
            "verdict": (
                f"{state['word_count']} 個字、{state['sentences']} 句，"
                f"主題看起來是：{'、'.join(ranked) if ranked else '（無明顯關鍵詞）'}"
            ),
        }

    # ── 節點 4：短文字的收尾 ───────────────────────────────────────

    async def too_short(state: State) -> dict:
        words = tokenise(state["text"])
        count = len(words)
        ctx.logger.warning("文字太短，跳過關鍵詞抽取", words=count)
        return {
            "unique_words": len(set(words)),
            "avg_word_length": round(sum(len(w) for w in words) / count, 2) if count else 0.0,
            "avg_sentence_length": round(count / state["sentences"], 2),
            "keywords": [],
            "verdict": f"只有 {count} 個字，太短，不做關鍵詞抽取",
        }

    # ── 接線 ───────────────────────────────────────────────────────

    graph.add_node("normalise", normalise)
    graph.add_node("analyse", analyse)
    graph.add_node("keywords", extract_keywords)
    graph.add_node("too_short", too_short)

    graph.add_edge(START, "normalise")
    graph.add_conditional_edges(
        "normalise",
        route,
        {"long_enough": "analyse", "too_short": "too_short"},
    )
    graph.add_edge("analyse", "keywords")
    graph.add_edge("keywords", END)
    graph.add_edge("too_short", END)

    return graph          # 未 compile —— 平台會自己 compile
