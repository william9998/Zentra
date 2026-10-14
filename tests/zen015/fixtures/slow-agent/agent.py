"""刻意慢的 Agent —— 用來驗證常駐容器的並發與溢出（ZEN-015 §7）。

**不是範例，是測試夾具。** 它存在的唯一理由是「佔住一個併發名額夠久，
久到可以觀察平台的反應」。快的 agent 沒辦法驗溢出：還沒來得及看到滿載就跑完了。

用 `async def` + `asyncio.sleep`，**不是 `time.sleep`** —— 後者會卡住整個
event loop，那樣測到的是「阻塞會癱瘓容器」（ADR-0012 已經量過），
不是「容器能不能同時跑 N 個」。
"""

import asyncio
import os
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from zentra_sdk import agent


class State(TypedDict, total=False):
    seconds: int
    slept: int


@agent(
    name="slow-agent",
    version="1.0.0",
    entrypoints=[{
        "name": "default",
        "description": "睡指定秒數再回報，用來佔住併發名額",
        "input_schema": {
            "type": "object",
            "properties": {"seconds": {"type": "integer", "minimum": 0, "maximum": 120}},
        },
        "output_schema": {
            "type": "object",
            "required": ["slept"],
            "properties": {"slept": {"type": "integer"}},
        },
    }],
)
def build(ctx) -> StateGraph:
    graph = StateGraph(State)

    async def sleep_node(state: State) -> dict:
        seconds = int(state.get("seconds") or 10)
        ctx.logger.info("開始睡", seconds=seconds, container=os.environ.get("HOSTNAME", "?"))
        await asyncio.sleep(seconds)
        return {"slept": seconds}

    graph.add_node("sleep", sleep_node)
    graph.add_edge(START, "sleep")
    graph.add_edge("sleep", END)
    return graph
