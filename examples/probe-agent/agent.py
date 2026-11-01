"""平台的測試 Agent —— 冒煙測試（`make smoke`）與壓測（`test/performance/`）都打它。

它走的是**和使用者 Agent 完全一樣的路**：`FROM zentra/agent-base` + `zentra_sdk`、
`docker push localhost:6000/probe-agent:<版號>` 註冊、驗證、發布、跑在常駐容器裡。
平台沒有任何為它開的特例。要測的東西一定要經過正式路徑，不然測到的不是使用者會遇到的。

## 兩種模式（同一個入口，用 input 的 `mode` 分流）

| `mode` | 做什麼 | 誰用 |
|---|---|---|
| `simulate`（預設） | 分類問題 → 模擬等待外部服務 → 組回答。**不打 LLM、不花錢、結果固定** | 冒煙測試、壓測 |
| `llm` | 真的呼叫 `ctx.llm()` 回答問題 | 手動測 LLM 路徑（要先在 Console 設好 LLM 憑證） |

不用兩個 entrypoint：不管呼叫哪個入口，平台跑的都是同一張 graph
（specs/agent-development.md §entrypoint），分流本來就得寫在 graph 裡。

## 為什麼要「模擬等待」

真實 Agent 的時間幾乎都花在等 LLM 或外部 API，不是在算東西。`work_ms` 讓壓測可以
模擬「一筆 Run 要等 2 秒」這種情況，量出常駐容器同時接很多筆、排隊時的行為，
而不必真的花 LLM 的錢。等待用 `asyncio.sleep`，**不卡住同一個容器裡的其他 Run** ——
這也是真實 Agent 該有的寫法（specs/agent-development.md §容器裡的限制）。

## 冒煙測試依賴的固定行為（改了要一起改 scripts/verify-zen000.sh）

- `simulate` 一定經過 `classify` → `wait` → `reply_short`（問題 ≤ 40 字）或 `reply_long`
- output 的 `echo` 一定等於輸入的 `question`
"""

import asyncio
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from zentra_sdk import agent

#: 超過這個字數就走「長問題」那條分支。
LONG_QUESTION_CHARS = 40

#: `work_ms` 上限。同步呼叫最多等 60 秒，留一點餘裕。
MAX_WORK_MS = 30_000


class State(TypedDict, total=False):
    """**每個 key 都會出現在 output 裡**（output 就是最終 state），所以只放輸入與結果。"""
    question: str     # 輸入
    mode: str         # 輸入（選填）：simulate / llm
    work_ms: int      # 輸入（選填）
    category: str     # classify 產出：short / long
    echo: str         # 原樣回傳的問題
    answer: str


@agent(
    name="probe-agent",
    version="1.0.0",
    entrypoints=[{
        "name": "default",
        "description": (
            "平台測試用 Agent。預設模式把問題分類成長或短、等待 work_ms 毫秒模擬呼叫外部服務，"
            "再回傳固定格式的回答，不呼叫 LLM；mode=llm 時改用 LLM 回答。"
        ),
        "input_schema": {
            "type": "object",
            "required": ["question"],
            "properties": {
                "question": {
                    "type": "string",
                    "description": "任意問題，會原樣出現在 output 的 echo",
                    "minLength": 1,
                },
                "mode": {
                    "type": "string",
                    "description": "simulate = 不打 LLM 的固定流程；llm = 真的呼叫 LLM",
                    "enum": ["simulate", "llm"],
                    "default": "simulate",
                },
                "work_ms": {
                    "type": "integer",
                    "description": "simulate 模式下模擬等待外部服務（例如 LLM）的毫秒數",
                    "default": 0,
                    "minimum": 0,
                    "maximum": MAX_WORK_MS,
                },
            },
        },
        "output_schema": {
            "type": "object",
            "required": ["echo", "category", "answer"],
            "properties": {
                "echo": {"type": "string"},
                "category": {"type": "string", "enum": ["short", "long"]},
                "answer": {"type": "string"},
            },
        },
    }],
)
def build(ctx) -> StateGraph:
    """驗證時也會呼叫（空設定、沒有 LLM），所以這裡**只組 graph，不做任何呼叫**。"""

    graph = StateGraph(State)

    async def classify(state: State) -> dict:
        question = state["question"]
        category = "long" if len(question) > LONG_QUESTION_CHARS else "short"
        ctx.logger.info("分類完成", chars=len(question), category=category)
        return {"category": category, "echo": question}

    def pick_mode(state: State) -> str:
        return "llm" if state.get("mode") == "llm" else "simulate"

    async def wait(state: State) -> dict:
        # 模擬等 LLM／外部 API。asyncio.sleep 不會卡住同一個容器裡的其他 Run。
        work_ms = state.get("work_ms") or 0
        if work_ms:
            ctx.emit("waiting", {"work_ms": work_ms})
            await asyncio.sleep(work_ms / 1000)
        return {}

    def pick_reply(state: State) -> str:
        return state["category"]

    async def reply_short(state: State) -> dict:
        return {"answer": f"收到短問題：{state['question']}"}

    async def reply_long(state: State) -> dict:
        question = state["question"]
        return {"answer": f"收到長問題（{len(question)} 字），開頭是：{question[:20]}…"}

    async def ask_llm(state: State) -> dict:
        # 系統提示放在 Console 的「設定」，改了不用發版
        system = ctx.config.get("SYSTEM_PROMPT", "用繁體中文簡短回答。")
        reply = await ctx.llm().ainvoke([("system", system), ("human", state["question"])])
        ctx.logger.info("LLM 回答完成", chars=len(reply.content))
        return {"answer": reply.content}

    graph.add_node("classify", classify)
    graph.add_node("wait", wait)
    graph.add_node("reply_short", reply_short)
    graph.add_node("reply_long", reply_long)
    graph.add_node("ask_llm", ask_llm)

    graph.add_edge(START, "classify")
    graph.add_conditional_edges("classify", pick_mode, {"simulate": "wait", "llm": "ask_llm"})
    graph.add_conditional_edges("wait", pick_reply, {"short": "reply_short", "long": "reply_long"})
    graph.add_edge("reply_short", END)
    graph.add_edge("reply_long", END)
    graph.add_edge("ask_llm", END)
    return graph          # 未 compile —— 平台會自己 compile
