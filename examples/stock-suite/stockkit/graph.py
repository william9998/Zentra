"""把 stockkit 的 tool 包成 LangGraph（P2：每個 Agent 最終都是一張 graph）。

一個 tool 就一個**同步**節點（`def`，不是 `async def`）：裡面會呼叫 `ctx.http`
（同步阻塞），LangGraph 會把同步節點丟到 worker thread 跑，不會卡住同一個常駐
容器裡的其他 Run。

輸出 = 最終 state（agent-development.md §4），所以 state 只放輸入欄位與信封欄位，
中間產物不進 state。
"""

from __future__ import annotations

from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from .common import normalize_security

_ENVELOPE_KEYS = ("tool", "schema_version", "as_of", "security", "data", "analysis", "warnings", "disclaimer")


class SecurityState(TypedDict, total=False):
    # 輸入
    symbol: str
    name: str
    market: str
    cost: float
    shares: float
    use_llm: bool
    # 輸出信封
    tool: str
    schema_version: str
    as_of: str
    security: dict
    data: dict
    analysis: Any
    warnings: list
    disclaimer: str


class PortfolioState(TypedDict, total=False):
    holdings: list
    use_llm: bool
    investor_profile: dict
    precomputed: list
    tool: str
    schema_version: str
    as_of: str
    data: dict
    analysis: Any
    warnings: list
    disclaimer: str


def security_graph(ctx: Any, tool: str,
                   run: Callable[[Any, dict[str, Any], bool], dict[str, Any]]) -> StateGraph:
    graph = StateGraph(SecurityState)

    def node(state: SecurityState) -> dict:
        security = normalize_security(dict(state))
        ctx.logger.info("開始", tool=tool, symbol=security["symbol"], market=security["market"],
                        use_llm=bool(state["use_llm"]))
        out = run(ctx, security, bool(state["use_llm"]))
        for warning in out["warnings"]:
            ctx.logger.warning(warning)
        return {k: out[k] for k in _ENVELOPE_KEYS if k in out}

    graph.add_node(tool.replace("-", "_"), node)
    graph.add_edge(START, tool.replace("-", "_"))
    graph.add_edge(tool.replace("-", "_"), END)
    return graph


def portfolio_graph(ctx: Any, tool: str, run: Callable[[Any, dict[str, Any]], dict[str, Any]]) -> StateGraph:
    graph = StateGraph(PortfolioState)

    def node(state: PortfolioState) -> dict:
        ctx.logger.info("開始", tool=tool, holdings=len(state["holdings"]),
                        precomputed=len(state.get("precomputed") or []), use_llm=bool(state["use_llm"]))
        out = run(ctx, dict(state))
        for warning in out["warnings"]:
            ctx.logger.warning(warning)
        update = {k: out[k] for k in _ENVELOPE_KEYS if k in out}
        # 輸出就是最終 state：不清掉的話，呼叫端送來的 precomputed（可能幾百 KB）
        # 會原封不動再回傳一次。用到的內容已經在 data.holdings[].parts 裡。
        update["precomputed"] = None
        return update

    graph.add_node("advise", node)
    graph.add_edge(START, "advise")
    graph.add_edge("advise", END)
    return graph
