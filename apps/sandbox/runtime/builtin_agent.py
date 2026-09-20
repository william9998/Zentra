"""ZEN-000 fixed fixture: an uncompiled two-node LangGraph.

The graph echoes the original string. It does not call an LLM or produce
custom progress events; the four node lifecycle events come from the platform
runtime, independent of whether user code uses the SDK.
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class EchoState(TypedDict, total=False):
    question: str
    echo: str


def build() -> StateGraph:
    graph = StateGraph(EchoState)

    async def echo_node(state: EchoState) -> dict[str, str]:
        return {"echo": state["question"]}

    async def finalize_node(state: EchoState) -> dict[str, str]:
        return {}

    graph.add_node("echo_node", echo_node)
    graph.add_node("finalize_node", finalize_node)
    graph.add_edge(START, "echo_node")
    graph.add_edge("echo_node", "finalize_node")
    graph.add_edge("finalize_node", END)
    return graph
