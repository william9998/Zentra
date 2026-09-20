"""zentra_sdk —— 使用者在沙盒內寫 Agent 時用的介面。

契約（SPEC §8.1）：

    from zentra_sdk import ZentraContext, agent
    from langgraph.graph import StateGraph

    @agent(name="my-agent", version="1.0.0")
    def build(ctx: ZentraContext) -> StateGraph:
        '''必須回傳**未 compile** 的 StateGraph；平台負責 compile 並注入事件 hook。'''
        ...

MVP 的 ctx 只有 llm / logger / emit / config。
`ctx.tools`、`ctx.knowledge`、`ctx.secrets` 在 MVP 外（SPEC §11），**不要自己加**。
"""

from zentra_sdk.context import AgentLogger, ZentraContext, agent

__all__ = ["AgentLogger", "ZentraContext", "agent"]
__version__ = "0.1.0"
