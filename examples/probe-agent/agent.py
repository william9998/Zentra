"""平台的測試 Agent —— 冒煙測試（`make smoke`）與壓測（`test/performance/`）都打它。

它走的是**和使用者 Agent 完全一樣的路**：`FROM zentra/agent-base` + `zentra_sdk`、
`docker push localhost:6000/probe-agent:<版號>` 註冊、驗證、發布、跑在常駐容器裡。
平台沒有任何為它開的特例。要測的東西一定要經過正式路徑，不然測到的不是使用者會遇到的。

## 兩種模式（同一個入口，用 input 的 `mode` 分流）

| `mode` | 做什麼 | 誰用 |
|---|---|---|
| `simulate`（預設） | 分類問題 → 載入資料 → 模擬等待外部服務 → 組回答。**不打 LLM、不花錢、結果固定** | 冒煙測試、壓測 |
| `llm` | 分類 → 載入資料 → 真的呼叫 `ctx.llm()`，把資料和問題一起送過去 | 手動測 LLM 路徑；壓測時配 `test/performance/mock_llm.py` |

不用兩個 entrypoint：不管呼叫哪個入口，平台跑的都是同一張 graph
（specs/agent-development.md §entrypoint），分流本來就得寫在 graph 裡。

## 模擬真實 Agent 的記憶體用量

真實 Agent 的時間幾乎都花在等 LLM 或外部 API，**等的這段期間，這筆 Run 的東西全留在容器記憶體裡**。
光是 `sleep` 量不出記憶體壓力 —— 一筆只帶幾個字的 Run 幾乎不佔記憶體。所以每個會佔記憶體的來源都有一個旋鈕：

| 真實 Agent | 旋鈕 |
|---|---|
| 使用者貼一大段文字進來 | `question` 本身的長度（壓測端的 `ZENTRA_QUESTION_CHARS`） |
| 讀進來的文件、對話歷史、工具查回來的資料，放在 state 裡 | `context_kb`：`load_context` 產生這麼多的資料，**等待期間一直留在 state 裡** |
| LLM 回來的長文字 | `simulate`：`reply_kb` 決定回答多長；`llm`：由 LLM（或假 LLM）決定 |
| 等 LLM 時連線掛著、回覆在路上 | `mode=llm` 時是真的連線：容器 → Gateway → LLM，Gateway 收完整段回覆才轉回容器 |

等待用 `asyncio.sleep`，**不卡住同一個容器裡的其他 Run** ——
這也是真實 Agent 該有的寫法（specs/agent-development.md §容器裡的限制）。

## 冒煙測試依賴的固定行為（改了要一起改 scripts/verify-zen000.sh）

- `simulate` 一定經過 `classify` → `load_context` → `wait` → `reply_short`（問題 ≤ 40 字）或 `reply_long`
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

#: `context_kb` / `reply_kb` 上限（100 MB）。故意比「容器 2 GB ÷ 同時 50 筆」大很多，
#: 才能用它把容器逼到 OOM、驗證平台怎麼收拾。
MAX_KB = 100 * 1024

#: 產生資料用的一段中文。一個中文字 UTF-8 佔 3 bytes。
_FILLER = "平台測試資料，模擬真實 Agent 讀進來的文件、對話歷史與工具查回來的結果。"


def _text_of_kb(kb: int) -> str:
    """產生約 `kb` KB（UTF-8）的文字。每次都是新的字串，不會被 Python 共用。"""
    if kb <= 0:
        return ""
    # 依字元數切，不先 encode 再切：那樣會在記憶體裡多出好幾份大字串，
    # 量到的峰值是這個函式造成的，不是 Agent 本身。
    chars = kb * 1024 * len(_FILLER) // len(_FILLER.encode())
    return (_FILLER * (chars // len(_FILLER) + 1))[:chars]


class State(TypedDict, total=False):
    """**每個 key 都會出現在 output 裡**（output 就是最終 state），所以只放輸入與結果。"""
    question: str     # 輸入
    mode: str         # 輸入（選填）：simulate / llm
    work_ms: int      # 輸入（選填）
    context_kb: int   # 輸入（選填）
    reply_kb: int     # 輸入（選填）
    category: str     # classify 產出：short / long
    context: str      # load_context 產出，回答前清空（見 _drop_context）
    echo: str         # 原樣回傳的問題
    answer: str


@agent(
    name="probe-agent",
    version="1.1.0",
    entrypoints=[{
        "name": "default",
        "description": (
            "平台測試用 Agent。預設模式把問題分類成長或短、載入 context_kb 的資料、"
            "等待 work_ms 毫秒模擬呼叫外部服務，再回傳 reply_kb 大小的回答，不呼叫 LLM；"
            "mode=llm 時把資料和問題一起交給 LLM 回答。"
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
                "context_kb": {
                    "type": "integer",
                    "description": "模擬讀進來的文件或對話歷史有多少 KB，整段 Run 期間都留在記憶體裡",
                    "default": 0,
                    "minimum": 0,
                    "maximum": MAX_KB,
                },
                "reply_kb": {
                    "type": "integer",
                    "description": "simulate 模式下回答有多少 KB，模擬 LLM 回來的長文字",
                    "default": 0,
                    "minimum": 0,
                    "maximum": MAX_KB,
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

    async def load_context(state: State) -> dict:
        # 模擬讀文件、撈對話歷史、查資料庫。放進 state，等待與回答期間都留著。
        context_kb = state.get("context_kb") or 0
        if context_kb:
            ctx.emit("context_loaded", {"context_kb": context_kb})
        return {"context": _text_of_kb(context_kb)}

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

    # 回答時把 context 清空：output 是整個最終 state，原文整包回傳會讓回應、
    # 資料庫裡的 output 一起變大 —— 真實 Agent 通常也只回答案，不回檢索到的原文。
    # 回應要多大另外用 reply_kb 控制，兩件事分開量。
    _drop_context = {"context": ""}

    async def reply_short(state: State) -> dict:
        answer = f"收到短問題：{state['question']}" + _text_of_kb(state.get("reply_kb") or 0)
        return {"answer": answer, **_drop_context}

    async def reply_long(state: State) -> dict:
        question = state["question"]
        answer = (f"收到長問題（{len(question)} 字），開頭是：{question[:20]}…"
                  + _text_of_kb(state.get("reply_kb") or 0))
        return {"answer": answer, **_drop_context}

    async def ask_llm(state: State) -> dict:
        # 系統提示放在 Console 的「設定」，改了不用發版
        system = ctx.config.get("SYSTEM_PROMPT", "用繁體中文簡短回答。")
        messages = [("system", system)]
        if state.get("context"):
            messages.append(("human", f"參考資料：\n{state['context']}"))
        messages.append(("human", state["question"]))
        reply = await ctx.llm().ainvoke(messages)
        ctx.logger.info("LLM 回答完成", chars=len(reply.content))
        return {"answer": reply.content, **_drop_context}

    graph.add_node("classify", classify)
    graph.add_node("load_context", load_context)
    graph.add_node("wait", wait)
    graph.add_node("reply_short", reply_short)
    graph.add_node("reply_long", reply_long)
    graph.add_node("ask_llm", ask_llm)

    graph.add_edge(START, "classify")
    graph.add_edge("classify", "load_context")
    graph.add_conditional_edges("load_context", pick_mode, {"simulate": "wait", "llm": "ask_llm"})
    graph.add_conditional_edges("wait", pick_reply, {"short": "reply_short", "long": "reply_long"})
    graph.add_edge("reply_short", END)
    graph.add_edge("reply_long", END)
    graph.add_edge("ask_llm", END)
    return graph          # 未 compile —— 平台會自己 compile
