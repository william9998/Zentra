"""invalid-at-runtime fixture：validate 過得了，但 node 執行時拋例外。"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from zentra_sdk import ZentraContext, agent

ENTRYPOINTS = [{
    "name": "default",
    "description": "Always raises",
    "input_schema": {"type": "object", "properties": {"question": {"type": "string"}},
                     "required": ["question"], "additionalProperties": False},
    "output_schema": {"type": "object", "properties": {"echo": {"type": "string"}},
                      "required": ["echo"]},
}]


@agent(name="qa-raises", version="1.0.0", entrypoints=ENTRYPOINTS)
def build(ctx: ZentraContext) -> StateGraph:
    graph = StateGraph(dict)

    async def boom(state):
        raise RuntimeError("deliberate user code failure")

    graph.add_node("boom", boom)
    graph.add_edge(START, "boom")
    graph.add_edge("boom", END)
    return graph
