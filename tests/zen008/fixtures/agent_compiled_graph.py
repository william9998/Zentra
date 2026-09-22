"""invalid fixture：build() 回傳**已 compile** 的 graph，必須在 VALIDATE 階段被擋下（P2）。"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from zentra_sdk import ZentraContext, agent

ENTRYPOINTS = [{
    "name": "default",
    "description": "Already compiled on purpose",
    "input_schema": {"type": "object", "properties": {"question": {"type": "string"}},
                     "required": ["question"]},
    "output_schema": {"type": "object", "properties": {"echo": {"type": "string"}},
                      "required": ["echo"]},
}]


@agent(name="qa-compiled", version="1.0.0", entrypoints=ENTRYPOINTS)
def build(ctx: ZentraContext):
    graph = StateGraph(dict)

    async def echo_node(state):
        return {"echo": state["question"]}

    graph.add_node("echo_node", echo_node)
    graph.add_edge(START, "echo_node")
    graph.add_edge("echo_node", END)
    return graph.compile()          # ← 違反 P2：平台才負責 compile
