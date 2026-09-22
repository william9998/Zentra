"""示範 Agent：符合 zentra.image/v1 契約的最小可用版本。"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from zentra_sdk import agent


class State(TypedDict, total=False):
    question: str
    echo: str


@agent(
    name="demo-echo",
    version="1.0.0",
    entrypoints=[{
        "name": "default",
        "description": "把問題原樣回傳",
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"echo": {"type": "string"}},
            "required": ["echo"],
        },
    }],
)
def build(ctx) -> StateGraph:
    graph = StateGraph(State)

    async def echo_node(state: State) -> dict:
        return {"echo": state["question"]}

    graph.add_node("echo_node", echo_node)
    graph.add_edge(START, "echo_node")
    graph.add_edge("echo_node", END)
    return graph
