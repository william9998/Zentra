"""valid-v1：兩個 entrypoint 的合法 Agent。`slow` 讓容器活得夠久可以 docker exec 探針。"""
from __future__ import annotations

import asyncio
from typing import Any

from langgraph.graph import END, START, StateGraph
from zentra_sdk import ZentraContext, agent

MARK = "v1"

ENTRYPOINTS = [
    {
        "name": "default",
        "description": "Echo the question back",
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {"echo": {"type": "string"}, "mark": {"type": "string"}},
            "required": ["echo", "mark"],
        },
    },
    {
        "name": "slow",
        "description": "Sleep before echoing; used to observe a live sandbox",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "sleep_seconds": {"type": "integer", "minimum": 0, "maximum": 300},
            },
            "required": ["question", "sleep_seconds"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {"echo": {"type": "string"}, "mark": {"type": "string"}},
            "required": ["echo", "mark"],
        },
    },
]


@agent(name="qa-oci-fixture", version="1.0.0", entrypoints=ENTRYPOINTS)
def build(ctx: ZentraContext) -> StateGraph:
    graph = StateGraph(dict)

    async def echo_node(state: dict[str, Any]) -> dict[str, Any]:
        seconds = state.get("sleep_seconds")
        if isinstance(seconds, int) and seconds > 0:
            await asyncio.sleep(seconds)
        return {"echo": state["question"], "mark": MARK}

    graph.add_node("echo_node", echo_node)
    graph.add_edge(START, "echo_node")
    graph.add_edge("echo_node", END)
    return graph
